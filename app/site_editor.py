"""
Editor generale del sito per Miriam: testi e foto di pagine, articoli e prodotti (prezzi compresi).

Flusso (stesso schema dell'editor Home di ieri, ma per QUALSIASI contenuto):
  pagina di Miriam (docs/sito.html) -> anteprima -> "Conferma" -> coda GitHub -> live_server.py (telefono)
  -> publish_edit() qui dentro:
     1. rilegge l'oggetto ATTUALE da WordPress e ne salva il backup (JSON, solo i campi che toccheremo),
     2. controlla TUTTO in memoria prima di scrivere (testi trovati in modo univoco, prezzi validi, foto valide),
     3. carica le foto nuove nella libreria media,
     4. sostituisce SOLO i testi/URL indicati: il resto del contenuto resta identico (controllo "scheletro"),
     5. scrive, rilegge e confronta campo per campo (se non coincide: rollback immediato),
     6. controlla la pagina pubblica e restituisce un riepilogo per Telegram.
  restore_previous() rimette lo stato salvato prima dell'ultima modifica di quell'oggetto.

Come si trovano i testi: la pagina di Miriam legge il contenuto PUBBLICO (rendered); qui sul telefono si ha
il contenuto GREZZO (raw). Il testo da cambiare e' cercato per CONTENUTO (con le virgolette "tipografiche" di WordPress
normalizzate): se non e' trovato in modo univoco la richiesta viene rifiutata, mai indovinata.

Solo requests + Pillow: si testa offline con un WordPress finto (vedi test_site_editor.py).
"""
import hashlib
import hmac
import html
import io
import json
import re
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path

import requests


class EditError(Exception):
    """Errore 'educato': il messaggio si puo' mostrare/mandare su Telegram."""


KINDS = ("page", "post", "product")
HTML_FIELDS = {"page": ("content",), "post": ("content",), "product": ("short_description", "description")}
PLAIN_FIELDS = {"page": ("title",), "post": ("title", "excerpt"), "product": ("title",)}   # per i prodotti title = name

LABELS = {"title": "Titolo", "excerpt": "Riassunto", "regular_price": "Prezzo", "sale_price": "Prezzo in offerta"}
MAX_TEXT_EDITS = 80
MAX_IMAGE_EDITS = 10
MAX_TEXT_LEN = 4000
MAX_TITLE_LEN = 200
MAX_IMAGE_BYTES = 6 * 1024 * 1024
MIN_IMAGE_WIDTH = 300
OUT_IMAGE_WIDTH = 1600
PRICE_JUMP_LIMIT = Decimal("0.5")      # variazione > 50% del prezzo: serve la conferma esplicita ("price_ack")
PRICE_MAX = Decimal("9999.99")

# ------------------------------------------------------------------ normalizzazione testi
_TEXTURIZE = {ord(c): "'" for c in "‘’′"}
_TEXTURIZE.update({ord(c): '"' for c in "“”″„"})
_TEXTURIZE.update({ord("–"): "-", ord("—"): "-", ord("…"): "...", ord("×"): "x", ord(" "): " "})


def norm(s: str) -> str:
    """Forma confrontabile: entita' decodificate, virgolette/trattini 'tipografici' ricondotti ad ASCII, spazi compattati."""
    s = html.unescape(s or "").translate(_TEXTURIZE)
    s = s.replace("...", "...")
    return re.sub(r"\s+", " ", s).strip()


# ------------------------------------------------------------------ testi dentro l'HTML
TOKEN_RE = re.compile(r"(<!--.*?-->|<style\b.*?</style>|<script\b.*?</script>|<[^>]+>)", re.S | re.I)


LINE_RE = re.compile(r"(\n[ \t\r\n]*)")


def tokens(content: str) -> list:
    """Spezza il contenuto in pezzi alternati: indice pari = testo, dispari = tag/commento/style/script/a-capo.
    Il testo e' spezzato anche a ogni a-capo: WordPress (wpautop) trasforma ogni riga in un paragrafo o in un <br>,
    quindi nella pagina pubblica ogni riga e' un testo a se'."""
    out = []
    raw = TOKEN_RE.split(content or "")
    for i, part in enumerate(raw):
        if i % 2 == 0:
            out.extend(LINE_RE.split(part))       # segmenti (pari) e a-capo (dispari), sempre in numero dispari
        else:
            out.append(part)
    return out


def text_nodes(content: str) -> list:
    """Testi 'visibili' (fuori da tag, commenti di blocco, style e script), nell'ordine del documento."""
    parts = tokens(content)
    return [norm(parts[i]) for i in range(0, len(parts), 2) if norm(parts[i])]


def apply_text_edits(content: str, edits: list, label: str = "contenuto") -> str:
    """Sostituisce i testi indicati. edit = {old, new, n (indice tra i doppioni), count (quanti doppioni vede la pagina)}."""
    if not edits:
        return content
    parts = tokens(content)
    by_norm = {}
    for i in range(0, len(parts), 2):
        k = norm(parts[i])
        if k:
            by_norm.setdefault(k, []).append(i)
    targets = {}
    for e in edits:
        old = norm(e.get("old"))
        if not old:
            raise EditError("Una modifica di testo non indica il testo di partenza.")
        cands = by_norm.get(old, [])
        if not cands:
            raise EditError(f"Non trovo piu' questo testo nella pagina ({label}): «{old[:60]}». "
                            "Forse e' stata modificata da poco, oppure e' un testo generato dal sito.")
        n = int(e.get("n", 0) or 0)
        if len(cands) > 1 and int(e.get("count", 1) or 1) != len(cands):
            raise EditError(f"Il testo «{old[:60]}» compare piu' volte e non riesco a capire quale cambiare. Non tocco nulla.")
        if n < 0 or n >= len(cands):
            raise EditError(f"Il testo «{old[:60]}» non e' nella posizione attesa. Non tocco nulla.")
        idx = cands[n]
        new = clean_text(e.get("new"), MAX_TEXT_LEN, required=True)
        if idx in targets:
            raise EditError("Due modifiche puntano allo stesso testo.")
        if norm(new) != old or new != old:
            targets[idx] = new
    for idx, new in targets.items():
        t = parts[idx]
        lead = t[: len(t) - len(t.lstrip())]
        trail = t[len(t.rstrip()):]
        parts[idx] = lead + html.escape(new, quote=False) + trail
    return "".join(parts)


def clean_text(value, limit, required=False) -> str:
    value = "" if value is None else str(value)
    value = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", value)
    value = re.sub(r"[ \t]*\n[ \t]*", " ", value)
    value = re.sub(r" {2,}", " ", value).strip()
    if len(value) > limit:
        raise EditError(f"Testo troppo lungo (max {limit} caratteri): «{value[:40]}…»")
    if required and not value:
        raise EditError("Un testo non puo' essere lasciato vuoto.")
    return value


# ------------------------------------------------------------------ foto dentro l'HTML
IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.I)


def _url_variants(url: str) -> list:
    return [url, url.replace("/", "\\/")]          # nel JSON dei blocchi WordPress gli '/' sono scritti '\/'


def has_image(content: str, url: str) -> bool:
    return any(v in content for v in _url_variants(url))


def apply_image_edit(content: str, old_url: str, new_url: str, new_id) -> str:
    """Cambia la foto old_url con new_url: nei tag <img> (src, srcset/sizes tolti, classe wp-image-N, id del blocco)
    e ovunque compaia l'URL (sfondi CSS, JSON dei blocchi, link)."""
    if not has_image(content, old_url):
        raise EditError("Non trovo piu' la foto da sostituire nella pagina. Non tocco nulla.")
    old_ids = set()

    def fix_tag(m):
        tag = m.group(0)
        if old_url not in tag:
            return tag
        tag = tag.replace(old_url, new_url)
        tag = re.sub(r'\s(?:srcset|sizes)=("[^"]*"|\'[^\']*\')', "", tag)
        for oid in re.findall(r"wp-image-(\d+)", tag):
            old_ids.add(oid)
        if new_id:
            tag = re.sub(r"wp-image-\d+", f"wp-image-{new_id}", tag)
        return tag

    out = IMG_TAG_RE.sub(fix_tag, content)
    for v_old, v_new in zip(_url_variants(old_url), _url_variants(new_url)):
        out = out.replace(v_old, v_new)
    if new_id:
        for oid in old_ids:
            out = re.sub(r'(<!-- wp:image \{[^}]*?"id":)%s\b' % oid, lambda m: f"{m.group(1)}{new_id}", out)
    return out


def skeleton(content: str) -> str:
    """Il contenuto senza testi, URL delle foto, id e srcset: serve a verificare che il RESTO non sia cambiato."""
    parts = tokens(content)
    parts = [("§" if norm(p) else p) if i % 2 == 0 else p for i, p in enumerate(parts)]
    s = "".join(parts)
    s = re.sub(r'\s(?:srcset|sizes)=("[^"]*"|\'[^\']*\')', "", s)
    s = re.sub(r"wp-image-\d+", "wp-image-N", s)
    s = re.sub(r'"id":\d+', '"id":N', s)
    s = re.sub(r"https?:(?:\\?/){2}[^\s\"')<>]*?(?:\\?/)wp-content(?:\\?/)uploads(?:\\?/)[^\s\"')<>]*", "UPL", s)
    return s


# ------------------------------------------------------------------ foto: validazione e ricompressione
def prepare_image(raw: bytes) -> bytes:
    from PIL import Image
    if len(raw) > MAX_IMAGE_BYTES:
        raise EditError("Una foto pesa troppo (max 6 MB).")
    try:
        img = Image.open(io.BytesIO(raw))
        img.verify()
        img = Image.open(io.BytesIO(raw))
    except Exception:
        raise EditError("Un file non e' una foto valida.")
    if img.format not in ("JPEG", "PNG", "WEBP"):
        raise EditError("Formato foto non ammesso (usa JPG, PNG o WEBP).")
    if img.width < MIN_IMAGE_WIDTH:
        raise EditError(f"Una foto e' troppo piccola ({img.width} px di larghezza, ne servono almeno {MIN_IMAGE_WIDTH}).")
    img = img.convert("RGB")
    if img.width > OUT_IMAGE_WIDTH:
        img = img.resize((OUT_IMAGE_WIDTH, round(img.height * OUT_IMAGE_WIDTH / img.width)))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=86, optimize=True, progressive=True)
    return buf.getvalue()


# ------------------------------------------------------------------ prezzi
def parse_price(value, label, allow_empty=False):
    s = "" if value is None else str(value).strip().replace(",", ".")
    if s == "":
        if allow_empty:
            return ""
        raise EditError(f"Il {label} non puo' essere vuoto.")
    if not re.fullmatch(r"\d{1,4}(\.\d{1,2})?", s):
        raise EditError(f"{label.capitalize()} non valido: «{s}». Scrivi solo numeri, per esempio 14,99.")
    try:
        d = Decimal(s)
    except InvalidOperation:
        raise EditError(f"{label.capitalize()} non valido.")
    if d <= 0 or d > PRICE_MAX:
        raise EditError(f"{label.capitalize()} fuori dai limiti (da 0,01 a 9999,99).")
    return f"{d:.2f}"


def _same_number(a, b):
    try:
        return Decimal(a) == Decimal(b)
    except InvalidOperation:
        return False


def check_prices(cur: dict, req: dict, allow_prices: bool) -> dict:
    """Ritorna i prezzi da scrivere ({regular_price, sale_price}) oppure {} se non cambia nulla."""
    if not req:
        return {}
    if not allow_prices:
        raise EditError("Per cambiare i prezzi serve il codice segreto attivo sul sistema. Non modifico nulla.")
    if cur.get("type") != "simple":
        raise EditError("I prodotti con varianti hanno prezzi diversi per ogni variante: questi prezzi non si cambiano da qui.")
    reg = parse_price(req.get("regular_price"), "prezzo")
    sale = parse_price(req.get("sale_price"), "prezzo in offerta", allow_empty=True)
    if sale and Decimal(sale) >= Decimal(reg):
        raise EditError("Il prezzo in offerta deve essere piu' basso del prezzo normale.")
    old_reg = cur.get("regular_price") or ""
    old_sale = cur.get("sale_price") or ""
    # il prezzo che Miriam vedeva nella pagina deve essere ancora quello vero (catalogo vecchio o prezzo cambiato nel frattempo)
    for key, live in (("seen_regular", old_reg), ("seen_sale", old_sale)):
        if key in req:
            seen = str(req.get(key) or "").strip().replace(",", ".")
            same = (seen == "" and live == "") or (seen != "" and live != "" and _same_number(seen, live))
            if not same:
                raise EditError("Il prezzo di questo prodotto e' cambiato nel frattempo (non e' piu' quello che vedevi). "
                                "Ricarica la pagina e rifai la modifica.")
    changed = {}
    if old_reg == "" or Decimal(reg) != Decimal(old_reg):
        changed["regular_price"] = reg
    if (sale or "") != (f"{Decimal(old_sale):.2f}" if old_sale else ""):
        changed["sale_price"] = sale
    if changed and old_reg:
        try:
            jump = abs(Decimal(reg) - Decimal(old_reg)) / Decimal(old_reg)
        except InvalidOperation:
            jump = Decimal(0)
        if jump > PRICE_JUMP_LIMIT and not req.get("price_ack"):
            raise EditError(f"Il prezzo cambia di piu' del 50% (da {old_reg} a {reg}). Se e' voluto, conferma esplicitamente il prezzo.")
    return changed


# ------------------------------------------------------------------ WordPress
class WP:
    """Accesso REST a pagine, articoli e prodotti (account con Application Password)."""

    def __init__(self, site, user, app_password, timeout=60):
        self.site = site.rstrip("/")
        self.auth = (user, app_password)
        self.timeout = timeout

    def _url(self, kind, oid):
        base = {"page": "wp/v2/pages", "post": "wp/v2/posts", "product": "wc/v3/products"}[kind]
        return f"{self.site}/wp-json/{base}/{oid}"

    def get_object(self, kind, oid) -> dict:
        r = requests.get(self._url(kind, oid), params={"context": "edit"}, auth=self.auth, timeout=self.timeout)
        if r.status_code == 404:
            raise EditError("Questa pagina/prodotto non esiste piu'.")
        r.raise_for_status()
        j = r.json()
        if kind == "product":
            return {"title": j["name"], "short_description": j.get("short_description", ""), "description": j.get("description", ""),
                    "regular_price": j.get("regular_price", ""), "sale_price": j.get("sale_price", ""), "type": j.get("type"),
                    "images": [{"id": i["id"], "src": i["src"]} for i in j.get("images", [])],
                    "modified": j.get("date_modified"), "link": j.get("permalink"), "status": j.get("status")}
        out = {"title": j["title"]["raw"], "content": j["content"]["raw"], "modified": j.get("modified"),
               "link": j.get("link"), "status": j.get("status")}
        if kind == "post":
            out["excerpt"] = j["excerpt"]["raw"]
            out["featured_media"] = j.get("featured_media", 0)
        return out

    def set_fields(self, kind, oid, fields: dict):
        body = dict(fields)
        if kind == "product":
            if "title" in body:
                body["name"] = body.pop("title")
            if "image_ids" in body:
                body["images"] = [{"id": i} for i in body.pop("image_ids")]
        r = requests.post(self._url(kind, oid), json=body, auth=self.auth, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def upload_media(self, jpeg: bytes, filename: str):
        r = requests.post(f"{self.site}/wp-json/wp/v2/media", data=jpeg, auth=self.auth, timeout=self.timeout,
                          headers={"Content-Disposition": f'attachment; filename="{filename}"', "Content-Type": "image/jpeg"})
        r.raise_for_status()
        j = r.json()
        return j["id"], j["source_url"]

    def public_text(self, url):
        r = requests.get(url, params={"nocache": int(time.time())}, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        return r.text


# ------------------------------------------------------------------ backup
def _save_backup(backup_dir: Path, kind, oid, fields: dict, tag="PRIMA") -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    path = backup_dir / f"{stamp}_{kind}-{oid}_{tag}.json"
    n = 1
    while path.exists():
        path = backup_dir / f"{stamp}_{kind}-{oid}_{tag}_{n}.json"
        n += 1
    data = {"kind": kind, "id": oid, "saved_at": stamp, "fields": fields,
            "sha256": hashlib.sha256(json.dumps(fields, sort_keys=True, ensure_ascii=False).encode()).hexdigest()}
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    return path


# ------------------------------------------------------------------ pubblicazione
def _field_values(cur: dict, kind: str, names) -> dict:
    out = {}
    for n in names:
        if n == "image_ids":
            out[n] = [i["id"] for i in cur.get("images", [])]
        elif n in cur:
            out[n] = cur[n]
    return out


def publish_edit(wp: WP, backup_dir: Path, req: dict, allow_prices: bool = False) -> dict:
    """req = {kind, id, title:{old,new}, excerpt:{old,new}, texts:[{field,old,new,n,count}],
              images:[{field,old,image_base64}], prices:{regular_price,sale_price,price_ack}}"""
    kind = req.get("kind")
    if kind not in KINDS:
        raise EditError("Tipo di contenuto non valido.")
    try:
        oid = int(req.get("id"))
    except (TypeError, ValueError):
        raise EditError("Identificativo non valido.")
    texts = req.get("texts") or []
    images = req.get("images") or []
    if len(texts) > MAX_TEXT_EDITS or len(images) > MAX_IMAGE_EDITS:
        raise EditError("Troppe modifiche in una volta sola: dividi in piu' invii.")

    cur = wp.get_object(kind, oid)
    if cur.get("status") not in ("publish", "private", "draft", "pending", "future"):
        raise EditError("Questo contenuto non e' in uno stato modificabile.")
    exp = req.get("expect_modified")
    if exp and cur.get("modified") and str(exp)[:19] != str(cur["modified"])[:19]:
        raise EditError("Nel frattempo questo contenuto e' stato modificato da qualcun altro. Ricarica la pagina e rifai la modifica.")

    new = {}                                  # campo -> nuovo valore da scrivere
    summary = []                              # righe leggibili PRIMA -> DOPO

    # titoli ed estratti (testo semplice)
    for f in PLAIN_FIELDS[kind]:
        e = req.get(f)
        if e:
            if norm(e.get("old")) != norm(cur.get(f)):
                raise EditError(f"Il campo «{f}» e' cambiato nel frattempo. Ricarica la pagina.")
            val = clean_text(e.get("new"), MAX_TITLE_LEN if f == "title" else MAX_TEXT_LEN, required=(f == "title"))
            if val != cur.get(f):
                new[f] = val
                summary.append(f"{LABELS.get(f, f)}: «{cur.get(f)}» -> «{val}»")

    # testi dentro l'HTML
    html_new = {}
    for f in HTML_FIELDS[kind]:
        mine = [t for t in texts if t.get("field") == f]
        if mine:
            out = apply_text_edits(cur.get(f, ""), mine, f)
            if out != cur.get(f, ""):
                html_new[f] = out
                for t in mine:
                    if norm(t["new"]) != norm(t["old"]) or t["new"] != t["old"]:
                        summary.append(f"testo: «{norm(t['old'])[:50]}» -> «{norm(t['new'])[:50]}»")
    for t in texts:
        if t.get("field") not in HTML_FIELDS[kind]:
            raise EditError("Una modifica di testo riguarda un campo non ammesso.")

    # foto: prima si controlla tutto, poi si carica
    prepared = []
    for im in images:
        f = im.get("field")
        old = (im.get("old") or "").strip()
        if f not in HTML_FIELDS[kind] + ("gallery", "featured"):
            raise EditError("Una modifica di foto riguarda un campo non ammesso.")
        if not im.get("image_base64"):
            raise EditError("Manca la foto nuova.")
        import base64
        try:
            raw = base64.b64decode(im["image_base64"], validate=False)
        except Exception:
            raise EditError("Foto non leggibile.")
        jpeg = prepare_image(raw)
        if f in HTML_FIELDS[kind]:
            base = html_new.get(f, cur.get(f, ""))
            if not has_image(base, old):
                raise EditError("Non trovo piu' una delle foto da sostituire. Ricarica la pagina.")
        elif f == "gallery":
            if kind != "product" or not any(i["src"] == old for i in cur.get("images", [])):
                raise EditError("Non trovo la foto del prodotto da sostituire. Ricarica la pagina.")
        elif f == "featured" and kind != "post":
            raise EditError("L'immagine in evidenza esiste solo per gli articoli.")
        prepared.append((f, old, jpeg))

    # prezzi
    price_changes = check_prices(cur, req.get("prices"), allow_prices) if kind == "product" else {}
    if req.get("prices") and kind != "product":
        raise EditError("I prezzi esistono solo per i prodotti.")
    for k, v in price_changes.items():
        summary.append(f"{LABELS.get(k, k)}: {cur.get(k) or '-'} -> {v or '-'} EUR")

    if not new and not html_new and not prepared and not price_changes:
        return {"changed": False, "summary": [], "message": "Nessuna differenza rispetto a quello gia' pubblicato."}

    # tutto controllato: salvataggio del backup (solo i campi che toccheremo)
    touched = set(new) | set(html_new) | set(price_changes)
    for f, _o, _j in prepared:
        touched.add({"gallery": "image_ids", "featured": "featured_media"}.get(f, f))
    backup = _save_backup(backup_dir, kind, oid, _field_values(cur, kind, sorted(touched)))

    # caricamento foto e sostituzione
    uploaded = []
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for n, (f, old, jpeg) in enumerate(prepared):
        mid, url = wp.upload_media(jpeg, f"sito-{kind}{oid}-{stamp}-{n}.jpg")
        if not url.startswith(wp.site + "/wp-content/uploads/"):
            raise EditError("Il sito ha restituito un indirizzo foto inatteso. Mi fermo.")
        uploaded.append(url)
        summary.append(f"foto: {old.rsplit('/', 1)[-1]} -> {url.rsplit('/', 1)[-1]}")
        if f in HTML_FIELDS[kind]:
            html_new[f] = apply_image_edit(html_new.get(f, cur.get(f, "")), old, url, mid)
        elif f == "gallery":
            ids = new.get("image_ids") or [i["id"] for i in cur["images"]]
            pos = [i["src"] for i in cur["images"]].index(old)
            ids[pos] = mid
            new["image_ids"] = ids
        elif f == "featured":
            new["featured_media"] = mid

    # rete di sicurezza: a parte testi/foto il resto dei contenuti deve essere identico
    for f, val in html_new.items():
        if skeleton(val) != skeleton(cur.get(f, "")):
            raise EditError("Controllo interno fallito: oltre ai testi e alle foto risulterebbe diverso altro. Non pubblico.")
    new.update(html_new)
    new.update(price_changes)

    wp.set_fields(kind, oid, new)
    saved = wp.get_object(kind, oid)
    mismatch = []
    for k, v in new.items():
        if k == "image_ids":
            if [i["id"] for i in saved.get("images", [])] != v:
                mismatch.append(k)
        elif k in ("regular_price", "sale_price"):
            a, b = saved.get(k) or "", v or ""
            if (Decimal(a) if a else None) != (Decimal(b) if b else None):
                mismatch.append(k)
        elif saved.get(k) != v:
            mismatch.append(k)
    if mismatch:
        wp.set_fields(kind, oid, _field_values(cur, kind, list(new)))   # rollback immediato
        raise EditError(f"Dopo il salvataggio i dati non coincidono ({', '.join(mismatch)}): ho rimesso la versione precedente.")

    public_ok = None
    try:
        probe = next((norm(t["new"]) for t in texts if t.get("new")), None) or (norm(new["title"]) if "title" in new else None)
        if probe and saved.get("link"):
            public_ok = probe[:40] in norm(re.sub(r"<[^>]+>", " ", wp.public_text(saved["link"])))
    except Exception:
        pass
    return {"changed": True, "kind": kind, "id": oid, "title": saved.get("title"), "link": saved.get("link"),
            "summary": summary, "backup": str(backup), "images": uploaded, "public_ok": public_ok}


def restore_previous(wp: WP, backup_dir: Path, kind: str, oid) -> dict:
    """Rimette lo stato salvato PRIMA dell'ultima modifica di quell'oggetto."""
    if kind not in KINDS:
        raise EditError("Tipo di contenuto non valido.")
    files = sorted(p for p in backup_dir.glob(f"*_{kind}-{int(oid)}_PRIMA*.json") if "_USATO" not in p.name and "RIPRISTINO" not in p.name)
    if not files:
        raise EditError("Non ho nessuna versione precedente di questo contenuto da ripristinare.")
    target = files[-1]
    data = json.loads(target.read_text(encoding="utf-8"))
    cur = wp.get_object(kind, int(oid))
    _save_backup(backup_dir, kind, int(oid), _field_values(cur, kind, list(data["fields"])), tag="PRIMA-RIPRISTINO")
    wp.set_fields(kind, int(oid), data["fields"])
    saved = wp.get_object(kind, int(oid))
    for k, v in data["fields"].items():
        got = [i["id"] for i in saved.get("images", [])] if k == "image_ids" else saved.get(k)
        if k in ("regular_price", "sale_price"):
            if (Decimal(got) if got else None) != (Decimal(v) if v else None):
                raise EditError("Ripristino non verificato: controlla il contenuto e le revisioni WordPress.")
        elif got != v:
            raise EditError("Ripristino non verificato: controlla il contenuto e le revisioni WordPress.")
    target.rename(target.with_name(target.stem + "_USATO.json"))
    return {"restored_from": target.name, "kind": kind, "id": int(oid), "title": saved.get("title")}


# ------------------------------------------------------------------ firma richiesta
def verify_signature(pin: str, payload: dict, key="site_edit", max_age=900, seen: set | None = None) -> bool:
    """HMAC-SHA256(codice, ts + '|' + json della richiesta senza le foto). Richiesta >15 min o gia' vista: rifiutata."""
    try:
        ts = int(payload["ts"])
        sig = payload["sig"]
    except Exception:
        return False
    if abs(time.time() - ts) > max_age:
        return False
    if seen is not None and sig in seen:
        return False
    body = json.dumps(signable(payload.get(key, {})), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    expected = hmac.new(pin.encode(), f"{ts}|{body}".encode(), hashlib.sha256).hexdigest()
    ok = hmac.compare_digest(expected, str(sig))
    if ok and seen is not None:
        seen.add(sig)
    return ok


def signable(req: dict) -> dict:
    """La richiesta senza i dati delle foto (troppo grandi): contano testi, prezzi, id, azioni e URL delle foto vecchie."""
    out = dict(req)
    if "images" in out:
        out["images"] = [{k: v for k, v in im.items() if k not in ("image_base64", "image_mime")} for im in out["images"]]
    return out
