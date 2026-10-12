"""
Editor semplificato della HERO della Home (foto + testi sovrapposti) per Miriam.

La cliente compila un modulo (docs/home.html) -> la richiesta arriva nella coda
GitHub -> live_server.py (Termux) chiama publish_hero() qui dentro, che:
  1. scarica e salva il contenuto ATTUALE della Home (backup + meta),
  2. controlla che i marcatori MNP siano ancora presenti (altrimenti si ferma),
  3. valida/ripulisce i testi (HTML-escape, lunghezze massime, link solo da elenco),
  4. se c'e' una foto: la controlla con Pillow, la ricomprime e la carica in libreria media,
  5. riscrive SOLO il blocco hero e la variabile CSS della foto, lasciando intatto il resto,
  6. aggiorna la pagina via REST, rilegge e verifica byte-per-byte,
  7. controlla la Home pubblica e restituisce un riepilogo (per Telegram).
restore_previous() rimette il contenuto salvato prima dell'ultima pubblicazione.

Nessuna dipendenza da Flask/WooCommerce: solo requests + Pillow, cosi' si testa offline.
"""
import hashlib
import hmac
import html
import io
import json
import re
import time
from pathlib import Path

import requests

PAGE_ID = 362

HERO_RE = re.compile(r"(<!--MNP:HERO-START-->)(.*?)(<!--MNP:HERO-END-->)", re.S)
IMG_RE = re.compile(r"(/\*MNP:IMG\*/)(.*?)(/\*MNP:IMG-END\*/)", re.S)

# Destinazioni ammesse per il bottone (la cliente sceglie da un elenco, mai un URL libero)
TARGETS = {
    "shop": ("Tutto il negozio", "/shop/"),
    "gel": ("Gel costruttori", "/categoria-prodotto/gel-unghie/"),
    "color": ("Gel color", "/categoria-prodotto/color-gel/"),
    "base": ("Base e Top", "/categoria-prodotto/basi-finish/"),
    "accessori": ("Accessori", "/categoria-prodotto/attrezzi-utensili/"),
    "starter": ("Starter Kit", "/categoria-prodotto/starter-kit/"),
    "blog": ("Blog", "/blog/"),
}

# campo -> (lunghezza massima, obbligatorio)
LIMITS = {
    "kicker_bold": (30, False),
    "kicker_1": (40, False),
    "kicker_2": (40, False),
    "title_1": (14, True),
    "num_1": (3, False),
    "title_2": (14, False),
    "num_2": (3, False),
    "sub_1": (34, False),
    "sub_2": (34, False),
    "btn_text": (24, True),
}

DEFAULTS = {
    "kicker_bold": "Settembre",
    "kicker_1": "è il momento perfetto",
    "kicker_2": "per rinnovare il tuo kit",
    "title_1": "PRENDI",
    "num_1": "3",
    "title_2": "PAGHI",
    "num_2": "2",
    "sub_1": "Scegli i tuoi prodotti",
    "sub_2": "preferiti",
    "btn_text": "Scopri ora",
    "target": "shop",
}

MAX_IMAGE_BYTES = 6 * 1024 * 1024
MIN_IMAGE_WIDTH = 900
OUT_IMAGE_WIDTH = 1920


class HeroError(Exception):
    """Errore 'educato': il messaggio si puo' mostrare/mandare su Telegram."""


# ------------------------------------------------------------------ validazione
def clean_text(value, limit):
    value = "" if value is None else str(value)
    value = re.sub(r"[\x00-\x1f\x7f]", " ", value)   # niente caratteri di controllo
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) > limit:
        raise HeroError(f"Testo troppo lungo (max {limit} caratteri): «{value[:40]}…»")
    return value


def validate_fields(raw: dict) -> dict:
    out = {}
    for key, (limit, required) in LIMITS.items():
        out[key] = clean_text(raw.get(key), limit)
        if required and not out[key]:
            raise HeroError(f"Il campo «{key}» non puo' essere vuoto.")
    target = raw.get("target") or "shop"
    if target not in TARGETS:
        raise HeroError("Destinazione del bottone non valida.")
    out["target"] = target
    return out


# ------------------------------------------------------------------ rendering
def _lines(*parts):
    return "<br>".join(p for p in parts if p)


def render_hero(fields: dict, site: str) -> str:
    e = lambda s: html.escape(s, quote=True)
    f = {k: e(v) for k, v in fields.items() if k != "target"}
    kicker = _lines(f"<b>{f['kicker_bold']}</b>" if f["kicker_bold"] else "", f["kicker_1"], f["kicker_2"])
    t1 = f"{f['title_1'].upper()} <span class=\"n\">{f['num_1']}</span>" if f["num_1"] else f["title_1"].upper()
    if f["title_2"]:
        t2 = f"{f['title_2'].upper()} <span class=\"n\">{f['num_2']}</span>" if f["num_2"] else f["title_2"].upper()
        title = f"{t1}<br><span class=\"rose\">{t2}</span>"
    else:
        title = t1
    sub = _lines(f["sub_1"], f["sub_2"])
    href = site.rstrip("/") + TARGETS[fields["target"]][1]
    parts = ['<div class="mnp-hero">',
             '  <div class="mnp-hero__inner">',
             '    <div class="mnp-hero__text">']
    if kicker:
        parts.append(f'      <p class="mnp-kicker">{kicker}</p>')
    parts.append(f'      <div class="mnp-title">{title}</div>')
    if sub:
        parts.append(f'      <p class="mnp-sub">{sub}</p>')
    parts.append(f'      <a class="mnp-btn" href="{href}">{f["btn_text"]} <span class="arw">&#8594;</span></a>')
    parts += ['    </div>', '  </div>', '</div>']
    return "\n".join(parts)


def apply_to_content(content: str, fields: dict, site: str, image_url: str | None) -> str:
    """Riscrive blocco hero (e foto se indicata). Il resto del contenuto resta identico."""
    if len(HERO_RE.findall(content)) != 1 or len(IMG_RE.findall(content)) != 1:
        raise HeroError("Nella Home non trovo i marcatori attesi (qualcuno ha modificato la pagina a mano?). Non tocco nulla.")
    new = HERO_RE.sub(lambda m: f"{m.group(1)}\n{render_hero(fields, site)}\n{m.group(3)}", content, count=1)
    if image_url:
        if not image_url.startswith(site.rstrip("/") + "/wp-content/uploads/") or "'" in image_url or ")" in image_url:
            raise HeroError("URL della foto non valido.")
        new = IMG_RE.sub(lambda m: f"{m.group(1)}url('{image_url}'){m.group(3)}", new, count=1)
    return new


def parse_current(content: str) -> dict:
    """Legge i valori attuali dall'hero (serve per mostrare il 'prima' nelle notifiche)."""
    m = HERO_RE.search(content)
    if not m:
        return {}
    h = m.group(2)
    g = lambda pat: (re.search(pat, h, re.S).group(1) if re.search(pat, h, re.S) else "")
    strip = lambda s: html.unescape(re.sub(r"<[^>]+>", "", s)).strip()
    return {
        "kicker": strip(g(r'class="mnp-kicker">(.*?)</p>').replace("<br>", " / ")),
        "title": strip(g(r'class="mnp-title">(.*?)</div>').replace("<br>", " / ")),
        "sub": strip(g(r'class="mnp-sub">(.*?)</p>').replace("<br>", " / ")),
        "button": strip(g(r'class="mnp-btn"[^>]*>(.*?)</a>')),
        "href": g(r'class="mnp-btn" href="([^"]*)"'),
        "image": (re.search(r"/\*MNP:IMG\*/url\('([^']*)'\)", content) or [None, ""])[1],
    }


# ------------------------------------------------------------------ foto
def prepare_image(raw: bytes) -> bytes:
    from PIL import Image  # import qui: nei test senza foto non serve
    if len(raw) > MAX_IMAGE_BYTES:
        raise HeroError("La foto pesa troppo (max 6 MB).")
    try:
        img = Image.open(io.BytesIO(raw))
        img.verify()
        img = Image.open(io.BytesIO(raw))
    except Exception:
        raise HeroError("Il file non e' una foto valida.")
    if img.format not in ("JPEG", "PNG", "WEBP"):
        raise HeroError("Formato foto non ammesso (usa JPG, PNG o WEBP).")
    if img.width < MIN_IMAGE_WIDTH:
        raise HeroError(f"Foto troppo piccola ({img.width} px di larghezza, ne servono almeno {MIN_IMAGE_WIDTH}).")
    img = img.convert("RGB")
    if img.width > OUT_IMAGE_WIDTH:
        img = img.resize((OUT_IMAGE_WIDTH, round(img.height * OUT_IMAGE_WIDTH / img.width)))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=86, optimize=True, progressive=True)   # ricompressa: toglie anche i metadati
    return buf.getvalue()


# ------------------------------------------------------------------ WordPress
class WP:
    def __init__(self, site, user, app_password, timeout=60):
        self.site = site.rstrip("/")
        self.auth = (user, app_password)
        self.timeout = timeout

    def get_page_raw(self):
        r = requests.get(f"{self.site}/wp-json/wp/v2/pages/{PAGE_ID}", params={"context": "edit"},
                         auth=self.auth, timeout=self.timeout)
        r.raise_for_status()
        j = r.json()
        return j["content"]["raw"], j

    def set_page_content(self, content):
        r = requests.post(f"{self.site}/wp-json/wp/v2/pages/{PAGE_ID}", json={"content": content},
                          auth=self.auth, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def upload_media(self, jpeg: bytes, filename: str):
        r = requests.post(f"{self.site}/wp-json/wp/v2/media", data=jpeg, auth=self.auth, timeout=self.timeout,
                          headers={"Content-Disposition": f'attachment; filename="{filename}"',
                                   "Content-Type": "image/jpeg"})
        r.raise_for_status()
        return r.json()["source_url"]

    def latest_revision_id(self):
        try:
            r = requests.get(f"{self.site}/wp-json/wp/v2/pages/{PAGE_ID}/revisions", params={"per_page": 1},
                             auth=self.auth, timeout=self.timeout)
            r.raise_for_status()
            return r.json()[0]["id"]
        except Exception:
            return None


# ------------------------------------------------------------------ pubblicazione
def _save_backup(backup_dir: Path, content: str, meta: dict, tag: str) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    path = backup_dir / f"{stamp}_{tag}.html"
    path.write_text(content, encoding="utf-8", newline="")
    meta = {**meta, "saved_at": stamp, "sha256": hashlib.sha256(content.encode()).hexdigest(), "chars": len(content)}
    path.with_suffix(".json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def publish_hero(wp: WP, backup_dir: Path, raw_fields: dict, image_bytes: bytes | None = None) -> dict:
    """Ritorna un dict riepilogo; solleva HeroError per i casi 'gestiti'."""
    fields = validate_fields(raw_fields)
    site = wp.site

    before, page = wp.get_page_raw()
    backup = _save_backup(backup_dir, before, {"page": PAGE_ID, "modified": page.get("modified"), "status": page.get("status")}, "PRIMA")
    cur = parse_current(before)
    # controllo a secco prima di caricare foto: se i marcatori mancano ci fermiamo qui
    apply_to_content(before, fields, site, None)

    image_url = None
    if image_bytes:
        jpeg = prepare_image(image_bytes)
        image_url = wp.upload_media(jpeg, f"hero-{time.strftime('%Y%m%d-%H%M%S')}.jpg")

    after = apply_to_content(before, fields, site, image_url)
    if after == before:
        return {"changed": False, "backup": str(backup), "message": "Nessuna differenza rispetto a quello gia' pubblicato."}

    # rete di sicurezza: a parte hero e foto il resto deve essere identico
    strip = lambda c: IMG_RE.sub("", HERO_RE.sub("", c))
    if strip(before) != strip(after):
        raise HeroError("Controllo interno fallito: il resto della pagina risulterebbe diverso. Non pubblico.")

    wp.set_page_content(after)
    saved, _ = wp.get_page_raw()
    if saved != after:
        # tentativo di rollback immediato
        wp.set_page_content(before)
        raise HeroError("Dopo il salvataggio il contenuto non coincide: ho rimesso la versione precedente.")

    rev = wp.latest_revision_id()
    public_ok = None
    try:
        pub = requests.get(site + "/", params={"nocache": int(time.time())}, timeout=30).text
        public_ok = ("mnp-hero" in pub) and (html.escape(fields["btn_text"], quote=True) in pub)
    except Exception:
        pass
    return {"changed": True, "backup": str(backup), "revision": rev, "before": cur, "after": parse_current(after),
            "image_url": image_url, "public_ok": public_ok}


def restore_previous(wp: WP, backup_dir: Path) -> dict:
    """Rimette il contenuto salvato PRIMA dell'ultima pubblicazione (il file *_PRIMA piu' recente)."""
    files = sorted(backup_dir.glob("*_PRIMA.html"))
    if not files:
        raise HeroError("Non ho nessuna versione precedente da ripristinare.")
    target = files[-1]
    old = target.read_text(encoding="utf-8")
    if len(HERO_RE.findall(old)) != 1:
        raise HeroError("La versione salvata e' piu' vecchia del sistema di modifica: ripristinala a mano dalle revisioni WordPress.")
    current, page = wp.get_page_raw()
    _save_backup(backup_dir, current, {"page": PAGE_ID, "modified": page.get("modified")}, "PRIMA-RIPRISTINO")
    wp.set_page_content(old)
    saved, _ = wp.get_page_raw()
    if saved != old:
        raise HeroError("Ripristino non verificato: controlla la Home e le revisioni WordPress.")
    target.rename(target.with_name(target.stem + "_USATO.html"))
    target.with_suffix(".json").rename(target.with_name(target.stem + "_USATO.json"))
    return {"restored_from": target.name, "after": parse_current(old)}


# ------------------------------------------------------------------ firma richiesta (opzionale)
def verify_signature(pin: str, payload: dict, max_age=900, seen: set | None = None) -> bool:
    """La pagina firma la richiesta con HMAC-SHA256(codice, ts + '|' + json dei campi).
    Il codice non viaggia mai in chiaro; una richiesta vecchia di >15 min o gia' vista e' rifiutata."""
    try:
        ts = int(payload["ts"])
        sig = payload["sig"]
    except Exception:
        return False
    if abs(time.time() - ts) > max_age:
        return False
    if seen is not None and sig in seen:
        return False
    # le foto non entrano nella firma (troppo grandi): contano solo i campi di testo/azione
    body = json.dumps({k: v for k, v in payload.get("home_hero", {}).items() if k not in ("image_base64", "image_mime")},
                      sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    expected = hmac.new(pin.encode(), f"{ts}|{body}".encode(), hashlib.sha256).hexdigest()
    ok = hmac.compare_digest(expected, str(sig))
    if ok and seen is not None:
        seen.add(sig)
    return ok
