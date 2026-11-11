"""
Editor delle SCHEDE della Home ("Piu' prodotti, piu' possibilita'" + le caselle con foto: Calendario, Gel costruttori...).

La pagina docs/schede.html manda una richiesta "home_cards" nella coda GitHub -> live_server.py chiama publish_cards() qui dentro:
  1. legge la Home attuale e ne salva una copia completa (*_PRIMA-SCHEDE.html, separata dai backup della parte alta),
  2. controlla che il blocco fra <!--MNP:CATS-START--> e <!--MNP:CATS-END--> ci sia una volta sola e abbia la struttura
     attesa (rileggendolo e riscrivendolo deve venire identico byte per byte, altrimenti si ferma),
  3. valida i testi (lunghezze, niente HTML), i link (solo pagine del negozio) e le foto (solo URL della libreria media),
  4. carica le foto nuove in libreria media,
  5. riscrive SOLO quel blocco: tutto il resto della Home (parte alta, tema natalizio, CSS...) resta identico,
  6. salva, rilegge e confronta (se non coincide rimette subito la versione di prima).
restore_previous_cards() rimette solo il blocco delle schede salvato prima dell'ultima modifica alle schede.
"""
import html
import re
import time
from pathlib import Path

import requests

import home_editor as he

CATS_RE = re.compile(r"(<!--MNP:CATS-START-->\n)(.*?)(\n<!--MNP:CATS-END-->)", re.S)

MIN_CARDS, MAX_CARDS = 2, 6
DEFAULT_COLUMNS = 5           # il CSS della Home mette 5 colonne su computer: con un numero diverso si indica sul blocco
LIMITS = {"title": 40, "claim": 80, "label": 24, "alt": 100}
CARD_MIN_WIDTH = 500          # le schede sono quadrate e piccole (circa 260 px): 500 px bastano
CARD_OUT_WIDTH = 1000
LINK_PATH_RE = re.compile(r"^/(?:shop/|blog/|(?:prodotto|categoria-prodotto)/[a-z0-9-]+(?:/[a-z0-9-]+)*/)$")
IMG_PATH_RE = re.compile(r"^/wp-content/uploads/[A-Za-z0-9_./-]+\.(?:jpe?g|png|webp|gif)$", re.I)
POS_RE = re.compile(r"^(\d{1,3})% (\d{1,3})%$")


class CardsError(he.HeroError):
    """Errore 'educato' (messaggio mostrabile su Telegram). Sottoclasse di HeroError: live_server lo gestisce allo stesso modo."""


# ------------------------------------------------------------------ testo <-> HTML
def esc(s: str) -> str:
    """Come lo scrive WordPress nella Home: & < > scappati, apostrofi tipografici come &#8217; (cosi' rileggere e riscrivere da' lo stesso testo)."""
    return html.escape(s, quote=False).replace("'", "&#8217;").replace("’", "&#8217;")


def esc_attr(s: str) -> str:
    return esc(s).replace('"', "&quot;")


def unesc(s: str) -> str:
    return html.unescape(s)


# ------------------------------------------------------------------ rendering
def render_block(d: dict) -> str:
    """d = {title, claim, cards:[{href, img, alt, pos, label_1, label_2}]} gia' validato. Deve riprodurre ESATTAMENTE l'HTML della Home."""
    out = ['<div class="mnp-mid">',
           '  <div class="mnp-mid__inner">',
           f'    <div class="mnp-rule"><div class="mnp-script">{esc(d["title"])}</div></div>',
           '    <div class="mnp-heart">&#9825;</div>']
    if d["claim"]:
        out.append(f'    <p class="mnp-claim">{esc(d["claim"])}</p>')
    out += ['  </div>', '</div>', '']
    n = len(d["cards"])
    cols = "" if n == DEFAULT_COLUMNS else f' style="grid-template-columns:repeat({n},1fr)"'
    out += ['<div class="mnp-cats">', f'  <div class="mnp-cats__inner"{cols}>']
    for c in d["cards"]:
        style = f' style="object-position:{c["pos"]}"' if c["pos"] else ""
        label = esc(c["label_1"]) + (f"<br>{esc(c['label_2'])}" if c["label_2"] else "")
        out += [f'    <a class="mnp-card" href="{esc_attr(c["href"])}">',
                f'      <img src="{esc_attr(c["img"])}" alt="{esc_attr(c["alt"])}"{style} />',
                f'      <div class="mnp-card__label"><span>{label}</span><span class="arw">&#8594;</span></div>',
                '    </a>']
    out += ['  </div>', '</div>']
    return "\n".join(out)


MID_RE = re.compile(
    r'^<div class="mnp-mid">\n  <div class="mnp-mid__inner">\n'
    r'    <div class="mnp-rule"><div class="mnp-script">(?P<title>[^<]*)</div></div>\n'
    r'    <div class="mnp-heart">&#9825;</div>\n'
    r'(?:    <p class="mnp-claim">(?P<claim>[^<]*)</p>\n)?'
    r'  </div>\n</div>\n\n'
    r'<div class="mnp-cats">\n  <div class="mnp-cats__inner"(?: style="grid-template-columns:repeat\(\d+,1fr\)")?>\n'
    r'(?P<cards>.*)'
    r'  </div>\n</div>$', re.S)
CARD_RE = re.compile(
    r'    <a class="mnp-card" href="(?P<href>[^"]*)">\n'
    r'      <img src="(?P<img>[^"]*)" alt="(?P<alt>[^"]*)"(?: style="object-position:(?P<pos>[^"]*)")? />\n'
    r'      <div class="mnp-card__label"><span>(?P<label>[^<]*(?:<br>[^<]*)?)</span><span class="arw">&#8594;</span></div>\n'
    r'    </a>\n')


def parse_block(block: str) -> dict:
    """Legge titolo, frase e schede dal blocco. Solleva CardsError se la struttura non e' quella attesa."""
    m = MID_RE.match(block)
    if not m:
        raise CardsError("Il blocco delle schede nella Home ha una struttura inattesa (modificato a mano?). Non tocco nulla.")
    cards, pos = [], 0
    body = m.group("cards")
    for cm in CARD_RE.finditer(body):
        if cm.start() != pos:
            break
        pos = cm.end()
        l1, _, l2 = cm.group("label").partition("<br>")
        cards.append({"href": unesc(cm.group("href")), "img": unesc(cm.group("img")), "alt": unesc(cm.group("alt")),
                      "pos": cm.group("pos") or "", "label_1": unesc(l1), "label_2": unesc(l2)})
    if pos != len(body) or not cards:
        raise CardsError("Le schede nella Home hanno una struttura inattesa (modificate a mano?). Non tocco nulla.")
    d = {"title": unesc(m.group("title")), "claim": unesc(m.group("claim") or ""), "cards": cards}
    if render_block(d) != block:
        raise CardsError("Il blocco delle schede non si rilegge in modo identico: per sicurezza non tocco nulla.")
    return d


def get_block(content: str) -> str:
    found = CATS_RE.findall(content)
    if len(found) != 1:
        raise CardsError("Nella Home non trovo il blocco delle schede (marcatori MNP:CATS). Non tocco nulla.")
    return found[0][1]


def parse_content(content: str) -> dict:
    return parse_block(get_block(content))


def replace_block(content: str, block: str) -> str:
    get_block(content)
    return CATS_RE.sub(lambda m: m.group(1) + block + m.group(3), content, count=1)


# ------------------------------------------------------------------ validazione
def _text(value, limit, what, required=False):
    value = "" if value is None else str(value)
    value = re.sub(r"[\x00-\x1f\x7f]", " ", value)
    value = re.sub(r"\s+", " ", value).strip().replace("'", "’")   # nella Home gli apostrofi sono tipografici (&#8217;)
    if "<" in value or ">" in value:
        raise CardsError(f"{what}: i simboli < e > non sono ammessi.")
    if len(value) > limit:
        raise CardsError(f"{what}: testo troppo lungo (max {limit} caratteri): «{value[:40]}…»")
    if required and not value:
        raise CardsError(f"{what}: non puo' essere vuoto.")
    return value


def _site_url(value, site, path_re, what):
    value = str(value or "").strip()
    base = site.rstrip("/")
    if not value.startswith(base + "/") or not path_re.match(value[len(base):]):
        raise CardsError(f"{what} non valido: «{value[:80]}».")
    return value


def validate(raw: dict, site: str, current: dict) -> dict:
    """Ritorna il blocco da scrivere (senza le foto nuove, che hanno solo l'indice della scheda)."""
    if not isinstance(raw.get("cards"), list):
        raise CardsError("Richiesta senza schede.")
    n = len(raw["cards"])
    if not MIN_CARDS <= n <= MAX_CARDS:
        raise CardsError(f"Le schede devono essere da {MIN_CARDS} a {MAX_CARDS} (ne sono arrivate {n}).")
    current_imgs = {c["img"] for c in current["cards"]}
    current_links = {c["href"] for c in current["cards"]}
    out = {"title": _text(raw.get("title"), LIMITS["title"], "Titolo in corsivo", True),
           "claim": _text(raw.get("claim"), LIMITS["claim"], "Frase sotto il titolo"),
           "cards": []}
    for i, c in enumerate(raw["cards"], 1):
        if not isinstance(c, dict):
            raise CardsError(f"Scheda {i}: dati non validi.")
        what = f"Scheda {i}"
        l1 = _text(c.get("label_1"), LIMITS["label"], f"{what}, scritta riga 1", True)
        l2 = _text(c.get("label_2"), LIMITS["label"], f"{what}, scritta riga 2")
        alt = _text(c.get("alt"), LIMITS["alt"], f"{what}, descrizione della foto") or (l1 + (" " + l2 if l2 else ""))
        pos = str(c.get("pos") or "").strip()
        if pos:
            pm = POS_RE.match(pos)
            if not pm or int(pm.group(1)) > 100 or int(pm.group(2)) > 100:
                raise CardsError(f"{what}: inquadratura della foto non valida.")
            if pos == "50% 50%":
                pos = ""
        img = str(c.get("img") or "").strip()
        if img and img not in current_imgs:
            img = _site_url(img, site, IMG_PATH_RE, f"{what}: indirizzo della foto")
        href = str(c.get("href") or "").strip()
        if href not in current_links:
            href = _site_url(href, site, LINK_PATH_RE, f"{what}: link")
        out["cards"].append({"href": href,
                             "img": img, "alt": alt, "pos": pos, "label_1": l1, "label_2": l2})
    return out


def new_images(raw: dict, n_cards: int) -> dict:
    """{indice scheda (0..n-1): bytes} dalle foto nuove della richiesta."""
    import base64
    out = {}
    for im in raw.get("images") or []:
        try:
            idx = int(im.get("card"))
        except Exception:
            raise CardsError("Foto nuova senza scheda di destinazione.")
        if not 0 <= idx < n_cards or idx in out:
            raise CardsError("Foto nuova con una scheda di destinazione non valida.")
        try:
            out[idx] = base64.b64decode(im.get("image_base64") or "", validate=True)
        except Exception:
            raise CardsError(f"La foto nuova della scheda {idx + 1} non e' leggibile.")
        if not out[idx]:
            raise CardsError(f"La foto nuova della scheda {idx + 1} e' vuota.")
    return out


def prepare_card_image(raw: bytes) -> bytes:
    """Come home_editor.prepare_image ma per le schede: minimo 500 px, uscita max 1000 px, trasparenze su fondo bianco."""
    if len(raw) > he.MAX_IMAGE_BYTES:
        raise CardsError("La foto pesa troppo (max 6 MB).")
    try:
        from PIL import Image
    except ImportError:
        return he._prepare_without_pillow(raw, CardsError, CARD_MIN_WIDTH)
    import io
    try:
        img = Image.open(io.BytesIO(raw))
        img.verify()
        img = Image.open(io.BytesIO(raw))
    except Exception:
        raise CardsError("Il file non e' una foto valida.")
    if img.format not in ("JPEG", "PNG", "WEBP"):
        raise CardsError("Formato foto non ammesso (usa JPG, PNG o WEBP).")
    if img.width < CARD_MIN_WIDTH:
        raise CardsError(f"Foto troppo piccola ({img.width} px di larghezza, ne servono almeno {CARD_MIN_WIDTH}).")
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        img = bg
    img = img.convert("RGB")
    if img.width > CARD_OUT_WIDTH:
        img = img.resize((CARD_OUT_WIDTH, round(img.height * CARD_OUT_WIDTH / img.width)))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=86, optimize=True, progressive=True)
    return buf.getvalue()


def _slug(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s[:30] or "scheda"


# ------------------------------------------------------------------ riepilogo per Telegram
def card_name(c: dict) -> str:
    return (c["label_1"] + (" " + c["label_2"] if c["label_2"] else "")).strip()


def describe_changes(before: dict, after: dict) -> list:
    """Righe leggibili (Telegram) con le differenze prima -> dopo."""
    lines = []
    if before["title"] != after["title"]:
        lines.append(f"Titolo: «{before['title']}» → «{after['title']}»")
    if before["claim"] != after["claim"]:
        lines.append(f"Frase: «{before['claim']}» → «{after['claim']}»")
    nb, na = len(before["cards"]), len(after["cards"])
    if nb != na:
        lines.append(f"Numero di schede: {nb} → {na}")
    bys = {c["href"]: c for c in before["cards"]}
    for i, c in enumerate(after["cards"], 1):
        old = before["cards"][i - 1] if i <= nb else None
        same_link = old is not None and old["href"] == c["href"]
        if old is None or not same_link:
            old = bys.get(c["href"])      # stessa scheda spostata di posto?
        if old is None:
            lines.append(f"Scheda {i}: NUOVA «{card_name(c)}» → {c['href']}")
            continue
        diffs = []
        if card_name(old) != card_name(c) or (old["label_1"], old["label_2"]) != (c["label_1"], c["label_2"]):
            diffs.append(f"scritta «{old['label_1']}{' / ' + old['label_2'] if old['label_2'] else ''}» → "
                         f"«{c['label_1']}{' / ' + c['label_2'] if c['label_2'] else ''}»")
        if old["href"] != c["href"]:
            diffs.append(f"link {old['href']} → {c['href']}")
        if old["img"] != c["img"]:
            diffs.append(f"foto → {c['img']}")
        if old["pos"] != c["pos"]:
            diffs.append(f"inquadratura «{old['pos'] or 'centro'}» → «{c['pos'] or 'centro'}»")
        if old["alt"] != c["alt"]:
            diffs.append(f"descrizione foto «{old['alt']}» → «{c['alt']}»")
        moved = before["cards"].index(old) + 1 if old in before["cards"] else i
        where = f"Scheda {i}" + (f" (era la {moved})" if moved != i else "")
        if diffs:
            lines.append(f"{where} «{card_name(c)}»: " + "; ".join(diffs))
        elif moved != i:
            lines.append(f"{where} «{card_name(c)}»: spostata")
    after_links = {c["href"] for c in after["cards"]}
    for c in before["cards"]:
        if c["href"] not in after_links:
            lines.append(f"Scheda TOLTA: «{card_name(c)}» ({c['href']})")
    return lines


# ------------------------------------------------------------------ pubblicazione
def publish_cards(wp: he.WP, backup_dir: Path, raw: dict) -> dict:
    site = wp.site
    before, page = wp.get_page_raw()
    cur = parse_content(before)
    data = validate(raw, site, cur)
    photos = new_images(raw, len(data["cards"]))
    for i, c in enumerate(data["cards"]):
        if not c["img"] and i not in photos:
            raise CardsError(f"Scheda {i + 1}: manca la foto.")
    # controllo a secco PRIMA di caricare le foto (con un segnaposto): se qualcosa non va ci fermiamo qui
    dry = {**data, "cards": [{**c, "img": c["img"] or site + "/wp-content/uploads/x.jpg"} for c in data["cards"]]}
    replace_block(before, render_block(dry))

    uploaded = []
    for i in sorted(photos):
        jpeg = prepare_card_image(photos[i])
        name = f"scheda-{_slug(card_name(data['cards'][i]))}-{time.strftime('%Y%m%d-%H%M%S')}.jpg"
        url = wp.upload_media(jpeg, name)
        if not url.startswith(site.rstrip("/") + "/wp-content/uploads/") or not IMG_PATH_RE.match(url[len(site.rstrip("/")):]):
            raise CardsError("WordPress ha restituito un indirizzo della foto inatteso. Non pubblico.")
        data["cards"][i]["img"] = url
        uploaded.append(url)

    after = replace_block(before, render_block(data))
    if after == before:
        return {"changed": False, "backup": "", "message": "Nessuna differenza rispetto a quello gia' pubblicato."}
    strip = lambda c: CATS_RE.sub("", c)
    if strip(before) != strip(after):
        raise CardsError("Controllo interno fallito: il resto della pagina risulterebbe diverso. Non pubblico.")
    if parse_content(after) != data:
        raise CardsError("Controllo interno fallito: il blocco nuovo non si rilegge uguale. Non pubblico.")

    # copia dell'intera Home SOLO quando si sta per scrivere davvero: e' quella che usa "Torna alla versione di prima"
    # (un invio senza differenze o rifiutato non deve diventare la "versione di prima")
    backup = he._save_backup(backup_dir, before, {"page": he.PAGE_ID, "modified": page.get("modified"), "status": page.get("status"),
                                                  "what": "schede"}, "PRIMA-SCHEDE")
    wp.set_page_content(after)
    saved, _ = wp.get_page_raw()
    if saved != after:
        wp.set_page_content(before)
        raise CardsError("Dopo il salvataggio il contenuto non coincide: ho rimesso la versione precedente.")

    rev = wp.latest_revision_id()
    public_ok = None
    try:
        pub = requests.get(site + "/", params={"nocache": int(time.time())}, timeout=30).text
        public_ok = "mnp-cats" in pub and all(esc_attr(c["href"]) in pub for c in data["cards"])
    except Exception:
        pass
    return {"changed": True, "backup": str(backup), "revision": rev, "before": cur, "after": data,
            "changes": describe_changes(cur, data), "uploaded": uploaded, "public_ok": public_ok}


def restore_previous_cards(wp: he.WP, backup_dir: Path) -> dict:
    """Rimette SOLO il blocco delle schede salvato prima dell'ultima modifica alle schede (file *_PRIMA-SCHEDE.html piu' recente).
    La parte alta e il resto della Home restano come sono adesso. Prima di scrivere salva l'intera pagina (*_PRIMA-RIPRISTINO-SCHEDE)."""
    files = sorted(backup_dir.glob("*_PRIMA-SCHEDE.html"))
    if not files:
        raise CardsError("Non ho nessuna versione precedente delle schede da ripristinare.")
    target = files[-1]
    old = target.read_text(encoding="utf-8")
    old_block = get_block(old)
    old_data = parse_block(old_block)
    current, page = wp.get_page_raw()
    cur_data = parse_content(current)
    he._save_backup(backup_dir, current, {"page": he.PAGE_ID, "modified": page.get("modified"), "what": "schede"}, "PRIMA-RIPRISTINO-SCHEDE")
    new = replace_block(current, old_block)
    if CATS_RE.sub("", new) != CATS_RE.sub("", current):
        raise CardsError("Controllo interno fallito: il resto della pagina risulterebbe diverso. Non ripristino.")
    if new != current:
        wp.set_page_content(new)
        saved, _ = wp.get_page_raw()
        if saved != new:
            wp.set_page_content(current)
            raise CardsError("Ripristino non verificato: ho rimesso la versione di prima. Controlla la Home e le revisioni WordPress.")
    target.rename(target.with_name(target.stem + "_USATO.html"))
    target.with_suffix(".json").rename(target.with_name(target.stem + "_USATO.json"))
    return {"restored_from": target.name, "changed": new != current, "changes": describe_changes(cur_data, old_data), "after": old_data}


# ------------------------------------------------------------------ installazione dei marcatori (una volta sola, a mano)
PLAIN_RE = re.compile(r'\n(<div class="mnp-mid">\n.*?\n<div class="mnp-cats">\n.*?\n  </div>\n</div>)(?=\n)', re.S)


def add_markers(content: str) -> str:
    """Aggiunge <!--MNP:CATS-START/END--> intorno a titolo + schede (commenti HTML: la pagina pubblica non cambia).
    Usato una sola volta da editor_home/aggiungi_marcatori_schede.py."""
    if "MNP:CATS" in content:
        raise CardsError("I marcatori delle schede ci sono gia'.")
    found = PLAIN_RE.findall(content)
    if len(found) != 1:
        raise CardsError("Blocco delle schede non trovato in modo univoco.")
    new = PLAIN_RE.sub(lambda m: "\n<!--MNP:CATS-START-->\n" + m.group(1) + "\n<!--MNP:CATS-END-->", content, count=1)
    parse_content(new)   # deve rileggersi identico
    return new
