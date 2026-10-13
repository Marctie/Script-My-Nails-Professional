"""
Server (gira su Termux, tablet sempre acceso, gestito da Termux-Launcher come
gli altri bot) che riceve in tempo reale le scelte della cliente dal sito
pubblico GitHub Pages e pubblica SUBITO su WooCommerce:
  - "custom"    -> salva la foto caricata da lei e la pubblica
  - "no_change" -> registra solo che per ora va bene cosi' (nessuna azione)

NESSUN tunnel, NESSUN server esposto su internet. Il sito scrive ogni scelta
come file JSON nella cartella queue/ del repository GitHub (API Contents, PUT
del file), usando un fine-grained PAT ristretto SOLO a questo repository
("Contents: write", visibile pubblicamente nel sorgente della pagina - per
questo e' ristretto al minimo, revocabile in un click). Questo script legge
quella cartella via API GitHub (con un token diverso, privato, mai esposto),
elabora ogni richiesta e la cancella dalla coda.

Tutte le notifiche (ricezione, backup, upload, conferma/errore) vengono
mandate SOLO tramite il bot Telegram "My Nails Live" (gia' esistente),
direttamente alla chat dell'utente: e' un invio (sendMessage), non richiede
mai di "ricevere" nulla, quindi non soffre del limite per cui un bot non
vede i messaggi di un altro bot.

Gestito da Termux-Launcher (voce "nails_live" in bots.conf): avvio/stop/restart
tramite la dashboard su http://127.0.0.1:8765 o con
  bash ~/bots/Termux-Launcher/bot_ctl.sh nails_live start|stop|restart

Monitoraggio:
  - GET /api/health -> {"ok": true, "uptime_seconds": ...} (solo locale, 127.0.0.1)
  - GET /api/stats  -> contatori richieste/pubblicazioni/errori ed eventi recenti
  - Log leggibile in logs/live_server.log
"""
import base64
import json
import logging
import os
import re
import threading
import time
from pathlib import Path

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify

from upload import upload_to_media_library, set_product_image, wcapi  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "Marctie/Script-My-Nails-Professional")
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "master")
GITHUB_POLL_SECONDS = int(os.environ.get("GITHUB_POLL_SECONDS", "10"))

TELEGRAM_LIVE_BOT_TOKEN = os.environ.get("TELEGRAM_LIVE_BOT_TOKEN")
TELEGRAM_LIVE_CHAT_ID = os.environ.get("TELEGRAM_LIVE_CHAT_ID")

LOGS_DIR = ROOT / "logs"
LOGS_DIR.mkdir(exist_ok=True)
LOG_FILE = LOGS_DIR / "live_server.log"
PID_FILE = LOGS_DIR / "live_server.pid"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler()],
)
logger = logging.getLogger("live_server")

START_TIME = time.time()
STATS = {"requests": 0, "published": 0, "rejected": 0, "errors": 0, "recent_events": []}
MAX_RECENT_EVENTS = 50


def notify(text: str) -> None:
    if not TELEGRAM_LIVE_BOT_TOKEN or not TELEGRAM_LIVE_CHAT_ID:
        return
    if len(text) > 3900:                                   # limite di Telegram: 4096 caratteri per messaggio
        text = text[:3850] + "\n… (messaggio accorciato)"
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_LIVE_BOT_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_LIVE_CHAT_ID, "text": text},
            timeout=10,
        )
    except Exception:
        logger.exception("Impossibile inviare notifica Telegram")


def record_event(event: dict):
    event["timestamp"] = time.strftime("%Y-%m-%d %H:%M:%S")
    STATS["recent_events"].insert(0, event)
    del STATS["recent_events"][MAX_RECENT_EVENTS:]


# --- Login, sessioni e messaggi "chi ha fatto cosa" -------------------------
# Ogni utente (Miriam, Marco...) ha un codice personale: EDITOR_USERS="miriam=CODICE;marco=CODICE" nel .env.
# Il browser manda un evento firmato di login; le modifiche al sito sono accettate solo con firma valida E sessione attiva.
# La sessione si chiude da sola dopo SESSION_MAX_ACTIONS modifiche o SESSION_IDLE_MIN minuti di inattivita'.
# Logica in sessions.py. REQUIRE_LOGIN_FOTO=1 rende obbligatorio il login anche per le foto prodotto (default: no).
import sessions as editor_sessions  # noqa: E402

SESSIONS = editor_sessions.Sessions(
    ROOT / "status" / "editor_sessions.json",
    max_actions=int(os.environ.get("SESSION_MAX_ACTIONS", editor_sessions.DEFAULT_MAX_ACTIONS)),
    idle_min=int(os.environ.get("SESSION_IDLE_MIN", editor_sessions.DEFAULT_IDLE_MIN)),
)
REQUIRE_LOGIN_FOTO = os.environ.get("REQUIRE_LOGIN_FOTO", "0") == "1"
_users_cache: dict | None = None
_queue_busy = threading.Event()        # attivo mentre si elabora la coda: il controllo di inattivita' aspetta
_failed_login_last: dict = {}


def get_users() -> dict:
    """Utenti e chiavi (la derivazione PBKDF2 costa circa un secondo: si fa una volta sola)."""
    global _users_cache
    if _users_cache is None:
        _users_cache = editor_sessions.parse_users(os.environ.get("EDITOR_USERS", ""))
    return _users_cache


def user_name(uid) -> str:
    u = get_users().get(str(uid or "").lower())
    return u["name"] if u else (str(uid)[:30] if uid else "?")


def fmt_t(ts: float) -> str:
    return time.strftime("%d/%m/%Y %H:%M", time.localtime(ts))


def fmt_dur(sec: int) -> str:
    m, s = divmod(int(sec), 60)
    return f"{m} min {s:02d} s" if m else f"{s} s"


def session_text(summ: dict, head: str) -> str:
    lines = [f"{head} — {user_name(summ['user'])}",
             f"🕒 Dal {fmt_t(summ['started'])} al {fmt_t(summ['ended'])} (durata {fmt_dur(summ['duration_s'])})",
             f"📌 Motivo: {summ['reason']}"]
    if summ.get("device"):
        lines.append(f"📱 Dispositivo: {summ['device']}")
    acts = summ.get("actions") or []
    lines.append(f"📝 Modifiche fatte in questa sessione: {len(acts)}")
    for i, a in enumerate(acts, 1):
        lines.append(f"  {i}) {time.strftime('%H:%M', time.localtime(a['t']))} — {a['text']}")
    return "\n".join(lines)


def authenticate(payload: dict, obj: dict, what: str):
    """Verifica la firma. Ritorna l'id utente oppure None (con avviso Telegram dettagliato)."""
    # la firma vale 15 minuti rispetto a QUANDO LA RICHIESTA E' ENTRATA IN CODA (non a quando la elaboro): se il telefono e' spento per ore
    # le richieste restano valide; un vecchio payload ripresentato dal Worker ha un orario di coda nuovo ma una firma vecchia, quindi si rifiuta
    uid = editor_sessions.verify(get_users(), payload, obj, seen=None, now=payload.get("_queued_at"))
    if uid and str(payload.get("sig")) in _home_seen_sigs:      # gia' elaborata: replay
        uid = None
    if not uid:
        STATS["rejected"] += 1
        claimed = str(payload.get("user") or "nessuno")[:30]
        logger.warning(f"Richiesta '{what}' rifiutata: firma non valida (utente dichiarato: {claimed}).")
        notify(f"🚫 RICHIESTA RIFIUTATA ({what}): firma non valida, scaduta o gia' usata.\n"
               f"👤 Utente dichiarato: {claimed}\n🕒 {fmt_t(time.time())}\nNessuna modifica fatta al sito.")
    return uid


def _mark_seen(payload: dict) -> None:
    """Da chiamare quando una richiesta firmata e' CHIUSA (non se va ritentata): da qui in poi la stessa firma e' un replay."""
    if payload.get("sig"):
        _home_seen_sigs.add(str(payload["sig"]))


def require_session(uid: str, what: str, at=None) -> bool:
    if SESSIONS.active(uid, at):
        SESSIONS.touch(uid, at)
        return True
    STATS["rejected"] += 1
    notify(f"🚫 {user_name(uid)} ha provato a fare una modifica ({what}) SENZA una sessione attiva "
           f"(login non fatto, scaduta per inattivita' o chiusa dopo {SESSIONS.max_actions} modifiche).\n"
           f"🕒 {fmt_t(time.time())}\nNessuna modifica fatta al sito: deve rifare il login.")
    return False


def record_session_action(uid: str, text: str, at=None) -> str:
    """Registra la modifica nella sessione. Ritorna una riga 'Modifica n/N' e, se il limite e' raggiunto, avvisa della chiusura."""
    if not SESSIONS.active(uid, at):
        return "Modifica fuori sessione (non conteggiata)"
    r = SESSIONS.record_action(uid, text, at)
    if r["closed"]:
        notify(session_text(r["summary"], "🔒 LOGOUT AUTOMATICO (limite modifiche)"))
    return f"Modifica {r['count']} di {r['max']} della sessione"


def describe_request(data: dict) -> list:
    """Cosa chiede la richiesta, in righe leggibili (usato per 'ricevuta' e per i rifiuti)."""
    out = []
    for f in ("title", "excerpt"):
        if data.get(f):
            out.append(f"{'Titolo' if f == 'title' else 'Riassunto'}: «{data[f].get('old','')[:300]}» → «{data[f].get('new','')[:300]}»")
    for t in data.get("texts") or []:
        out.append(f"Testo: «{str(t.get('old',''))[:300]}» → «{str(t.get('new',''))[:300]}»")
    for im in data.get("images") or []:
        old = str(im.get("old", "")).rsplit("/", 1)[-1] or "(nessuna)"
        out.append(f"Foto: {old} → nuova foto caricata ({im.get('field')})")
    p = data.get("prices")
    if p:
        out.append(f"Prezzo: {p.get('seen_regular','?')} → {p.get('regular_price','?')} €  |  offerta: {p.get('seen_sale') or '-'} → {p.get('sale_price') or '-'}")
    return out


def process_session_event(payload: dict) -> bool:
    ev = payload.get("session_event") or {}
    typ = ev.get("type")
    if typ == "login_failed":                     # non firmato (chi sbaglia non ha la chiave): solo avviso, con limite anti-spam
        who = str(ev.get("user_try") or "?")[:30]
        if time.time() - _failed_login_last.get(who, 0) > 60:
            _failed_login_last[who] = time.time()
            notify(f"⚠️ TENTATIVO DI ACCESSO FALLITO — utente «{who}», codice errato.\n🕒 {fmt_t(time.time())}\n📱 {str(ev.get('device',''))[:120]}")
        return True
    uid = authenticate(payload, ev, f"sessione/{typ}")
    if not uid:
        return True
    name = user_name(uid)
    if typ == "login":
        r = SESSIONS.login(uid, str(ev.get("device", "")), payload.get("_queued_at"))
        if r["previous"]:
            notify(session_text(r["previous"], "🔁 SESSIONE PRECEDENTE CHIUSA (nuovo login)"))
        notify(f"🔓 LOGIN — {name} ha effettuato l'accesso\n🕒 {fmt_t(time.time())}\n📱 Dispositivo: {str(ev.get('device',''))[:120]}\n"
               f"La sessione si chiude dopo {SESSIONS.max_actions} modifiche o {SESSIONS.idle_s // 60} minuti di inattivita'.")
    elif typ == "logout":
        summ = SESSIONS.logout(uid, str(ev.get("reason") or "uscita volontaria")[:80], payload.get("_queued_at"))
        if summ:
            notify(session_text(summ, "🔒 LOGOUT"))
        else:
            logger.info(f"Logout di {uid} ignorato: nessuna sessione aperta (gia' chiusa).")
    else:
        logger.warning(f"Evento di sessione sconosciuto: {typ}")
    _mark_seen(payload)
    return True


def session_sweeper_loop():
    """Ogni minuto chiude le sessioni rimaste inattive e manda il riepilogo su Telegram."""
    while True:
        time.sleep(60)
        try:
            if _queue_busy.is_set():
                continue
            for summ in SESSIONS.sweep():
                notify(session_text(summ, "⏳ SESSIONE SCADUTA (nessun logout)"))
        except Exception:
            logger.exception("Errore nel controllo delle sessioni")


def processed_dir_for(category: str) -> Path:
    return ROOT / "processed" / category


def backup_manifest_path_for(category: str) -> Path:
    return ROOT / "backup" / category / "manifest" / "manifest.json"


def load_json(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def find_manifest_entry(category: str, product_id: str):
    manifest = load_json(backup_manifest_path_for(category), [])
    return next((e for e in manifest if str(e["product_id"]) == str(product_id)), None)


def save_custom_image_bytes(category: str, product_id: str, content: bytes, mime: str) -> Path:
    ext = {"image/png": ".png", "image/webp": ".webp"}.get(mime, ".jpg")
    overrides_dir = processed_dir_for(category) / "client_overrides"
    overrides_dir.mkdir(parents=True, exist_ok=True)
    dest = overrides_dir / f"{product_id}{ext}"
    dest.write_bytes(content)
    return dest


def backup_live_image(category: str, product_id: int):
    """Scarica e salva la foto ATTUALMENTE live su WooCommerce prima di sostituirla,
    cosi' c'e' sempre un backup fresco del prodotto appena prima di ogni pubblicazione."""
    try:
        resp = wcapi.get(f"products/{product_id}")
        resp.raise_for_status()
        images = resp.json().get("images") or []
        if not images:
            return
        url = images[0]["src"]
        ext = Path(url.split("?")[0]).suffix or ".jpg"
        backup_dir = processed_dir_for(category) / "pre_publish_backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        dest = backup_dir / f"{product_id}_{stamp}{ext}"
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        dest.write_bytes(r.content)
        logger.info(f"[{category}] backup foto live prodotto {product_id} -> {dest.name}")
        notify(f"⬇️ My Nails: scaricata e salvata la foto attuale del prodotto {product_id} ({category}) prima della sostituzione.")
    except Exception as e:
        logger.warning(f"[{category}] backup foto live fallito per {product_id}: {e}")
        notify(f"⚠️ My Nails: backup della foto attuale FALLITO per il prodotto {product_id} ({category}): {e}")


def current_image_filename(product_id: int) -> str | None:
    """Nome file dell'immagine ATTUALMENTE live su WooCommerce (prima della
    sostituzione), preso dall'URL. Serve per rinominare la nuova foto con lo
    stesso nome di quella che va a sostituire, invece del nome generato da
    ChatGPT/altro strumento lato cliente (es. "image (3).png")."""
    try:
        resp = wcapi.get(f"products/{product_id}")
        resp.raise_for_status()
        images = resp.json().get("images") or []
        if not images:
            return None
        url = images[0]["src"]
        return Path(url.split("?")[0]).name
    except Exception as e:
        logger.warning(f"Impossibile leggere il nome file attuale per il prodotto {product_id}: {e}")
        return None


def rename_to_match_original(category: str, product_id: int, image_path: Path) -> Path:
    """Rinomina image_path in modo che il NOME (senza estensione) coincida con
    quello della foto attualmente live, mantenendo l'estensione reale del file
    caricato (il formato puo' differire, es. cliente carica un .png al posto
    di un .jpg)."""
    original_name = current_image_filename(product_id)
    if not original_name:
        return image_path
    target_stem = Path(original_name).stem
    new_path = image_path.with_name(f"{target_stem}{image_path.suffix}")
    if new_path == image_path:
        return image_path
    image_path.replace(new_path)
    logger.info(f"[{category}] {product_id}: foto rinominata in '{new_path.name}' (era '{image_path.name}') per corrispondere all'originale.")
    return new_path


def publish_image(category: str, product_id: int, image_path: Path):
    backup_live_image(category, product_id)
    image_path = rename_to_match_original(category, product_id, image_path)
    media_id = upload_to_media_library(image_path)
    set_product_image(product_id, media_id)
    return media_id


def process_choice(category: str, product_id: str, status: str, image_bytes: bytes = None, image_mime: str = "", uid: str = "", at=None) -> bool:
    """Ritorna True se la voce puo' essere considerata definita (pubblicata
    con successo, o registrata senza bisogno di pubblicare) e quindi rimossa
    dalla coda; False se la pubblicazione e' fallita e andrebbe ritentata
    (vedi QUEUE_MAX_ATTEMPTS in github_queue_polling_loop) prima di darla
    per persa."""
    STATS["requests"] += 1
    who = f"\n👤 Utente: {user_name(uid)}  🕒 {fmt_t(time.time())}" if uid else ""

    review_state_path = processed_dir_for(category) / "review_state.json"
    review_state = load_json(review_state_path, {})
    review_state[product_id] = status
    save_json(review_state_path, review_state)

    entry = find_manifest_entry(category, product_id)
    if entry is None:
        STATS["errors"] += 1
        logger.error(f"Prodotto {product_id} non trovato in categoria {category}")
        notify(f"⚠️ My Nails: prodotto {product_id} non trovato in categoria {category}, scelta ignorata.{who}")
        return True  # non c'e' nulla da ritentare: il prodotto non esiste nel manifest

    log_path = processed_dir_for(category) / "live_publish_log.json"
    log = load_json(log_path, {})
    product_name = entry.get("name", product_id)

    try:
        if status == "custom" and image_bytes:
            notify(f"📩 My Nails: ricevuta una foto dalla cliente per \"{product_name}\" ({category}), avvio pubblicazione...{who}")
            image_path = save_custom_image_bytes(category, product_id, image_bytes, image_mime)
            notify(f"💾 My Nails: foto della cliente salvata in locale per \"{product_name}\" ({category}).")
            media_id = publish_image(category, entry["product_id"], image_path)
            log[product_id] = {"status": "pubblicato", "media_id": media_id, "source": "foto cliente"}
            save_json(log_path, log)
            STATS["published"] += 1
            record_event({"category": category, "product_id": product_id, "action": "pubblicato (foto cliente)"})
            logger.info(f"[{category}] {product_id} pubblicato (foto cliente), media_id={media_id}")
            counter = record_session_action(uid, f"foto prodotto «{product_name}» ({category})", at) if uid else ""
            notify(
                f"⬆️ My Nails: nuova foto caricata su WordPress (media_id={media_id}) e impostata come "
                f"immagine di \"{product_name}\" ({category}) su mynailsprofessional.it. ✅ Pubblicato."
                f"{who}" + (f"\n🔢 {counter}" if counter else "")
            )
        else:
            log[product_id] = {"status": "registrato, nessuna pubblicazione", "review": status}
            save_json(log_path, log)
            STATS["rejected"] += 1
            record_event({"category": category, "product_id": product_id, "action": f"registrato ({status})"})
            logger.info(f"[{category}] {product_id} registrato senza pubblicazione (status={status})")
            notify(f"✔️ My Nails: \"{product_name}\" ({category}) confermato dalla cliente, nessuna modifica alla foto.{who}")
        return True
    except Exception as e:
        STATS["errors"] += 1
        record_event({"category": category, "product_id": product_id, "action": f"errore: {e}"})
        logger.exception(f"[{category}] {product_id} errore durante la pubblicazione")
        notify(f"⚠️ My Nails: pubblicazione fallita per \"{product_name}\" ({category}), prodotto {product_id}: {e} — ritento automaticamente.{who}")
        return False


# --- Editor della Home (hero: foto + testi) ---------------------------------
# Richieste con chiave "home_hero" nel JSON di coda. Logica in home_editor.py.
HOME_EDITOR_PIN = os.environ.get("HOME_EDITOR_PIN")  # facoltativo: se impostato, le richieste devono essere firmate
HOME_BACKUP_DIR = ROOT / "home_editor_backups"
WP_USERNAME_HOME = os.environ.get("WP_USERNAME", "")
WP_APP_PASSWORD_HOME = os.environ.get("WP_APP_PASSWORD", "")
_home_seen_sigs: set = set()
_home_lock = threading.Lock()


def process_home_hero(payload: dict) -> bool:
    """True = richiesta chiusa (ok o rifiutata); False = errore temporaneo, da ritentare.
    Serve il login: richiesta firmata dall'utente + sessione attiva."""
    import home_editor as he

    STATS["requests"] += 1
    data = payload.get("home_hero") or {}
    action = data.get("action", "publish")
    if not payload.get("user"):
        STATS["rejected"] += 1
        notify(f"🚫 RICHIESTA RIFIUTATA (Home): arrivata senza login (pagina vecchia o non autorizzata).\n🕒 {fmt_t(time.time())}\n"
               "Nessuna modifica fatta al sito.")
        return True
    uid = authenticate(payload, data, "Home")
    if not uid:
        return True
    name = user_name(uid)
    if not require_session(uid, "Home", payload.get("_queued_at")):
        return True
    wanted = []
    if action == "restore":
        wanted.append("Chiede di tornare alla versione precedente della Home.")
    else:
        labels = [("kicker_bold", "Parola in alto"), ("kicker_1", "Riga 1"), ("kicker_2", "Riga 2"), ("title_1", "Titolo"), ("num_1", "N. 1"),
                  ("title_2", "Titolo 2"), ("num_2", "N. 2"), ("sub_1", "Frase 1"), ("sub_2", "Frase 2"), ("btn_text", "Bottone"), ("target", "Destinazione")]
        wanted = [f"{lab}: «{str(data.get(k, ''))[:120]}»" for k, lab in labels if data.get(k) not in (None, "")]
        wanted.append("Foto: " + ("NUOVA foto caricata" if data.get("image_base64") else "invariata"))
    notify(f"📥 {name} ha inviato una modifica alla HOME (parte in alto) — elaboro…\n🕒 {fmt_t(time.time())}\n" + "\n".join("• " + w for w in wanted))
    wp = he.WP(os.environ["WC_SITE_URL"], WP_USERNAME_HOME, WP_APP_PASSWORD_HOME)
    try:
        with _home_lock:
            if action == "restore":
                r = he.restore_previous(wp, HOME_BACKUP_DIR)
                STATS["published"] += 1
                record_event({"action": "home ripristinata"})
                counter = record_session_action(uid, "Home: ripristinata la versione precedente", payload.get("_queued_at"))
                notify(f"↩️ HOME RIPRISTINATA — 👤 {name}\nVersione ripristinata: {r['restored_from']}\n"
                       f"Ora: {r['after'].get('title')} | {r['after'].get('button')}\n🔢 {counter}")
                _mark_seen(payload)
                return True
            image_bytes = base64.b64decode(data["image_base64"]) if data.get("image_base64") else None
            r = he.publish_hero(wp, HOME_BACKUP_DIR, data, image_bytes)
    except he.HeroError as e:
        STATS["rejected"] += 1
        logger.warning(f"Home: richiesta non eseguita: {e}")
        notify(f"⚠️ HOME: modifica di {name} NON eseguita — {e}\nIl sito e' rimasto com'era.")
        _mark_seen(payload)
        return True
    except requests.RequestException as e:
        STATS["errors"] += 1
        logger.exception("Home: errore di rete/WordPress")
        notify(f"⚠️ HOME: errore temporaneo ({e}) per la modifica di {name} — ritento automaticamente.")
        return False

    _mark_seen(payload)
    if not r["changed"]:
        notify(f"ℹ️ HOME: nessuna differenza rispetto a quanto gia' pubblicato ({name}), non ho cambiato nulla.")
        return True
    STATS["published"] += 1
    record_event({"action": "home aggiornata", "revision": r.get("revision")})
    b, a = r["before"], r["after"]
    changes = [f"• {lab}: «{b.get(k)}» → «{a.get(k)}»" for k, lab in
               (("kicker", "Riga piccola"), ("title", "Titolo"), ("sub", "Frase"), ("button", "Bottone"), ("href", "Link del bottone"), ("image", "Foto"))
               if b.get(k) != a.get(k)]
    counter = record_session_action(uid, "Home: " + (", ".join(c.split(":")[0][2:] for c in changes) or "ritocchi"), payload.get("_queued_at"))
    notify(
        f"✅ HOME AGGIORNATA — 👤 {name}\n🕒 {fmt_t(time.time())}\n" + "\n".join(changes) + "\n"
        f"Foto: {'NUOVA ' + r['image_url'] if r.get('image_url') else 'invariata'}\n"
        f"Controllo pagina pubblica: {'OK' if r.get('public_ok') else 'DA VERIFICARE'}\n"
        f"Revisione WordPress: {r.get('revision')}\nBackup: {Path(r['backup']).name}\n🔢 {counter}\n"
        "Per tornare indietro: «Torna alla versione di prima» nella pagina della Home."
    )
    return True


# --- Editor del sito (pagine, articoli, prodotti: testi, foto, prezzi) -------
# Richieste con chiave "site_edit" nel JSON di coda. Logica in site_editor.py.
# Qui il codice segreto (HOME_EDITOR_PIN) e' OBBLIGATORIO: senza, ogni richiesta e' rifiutata.
SITE_BACKUP_DIR = ROOT / "site_editor_backups"


def refresh_catalog_async() -> None:
    """Rigenera docs/data/catalogo.json (prezzi e galleria dei prodotti, letti dalla pagina di Miriam) e lo scrive su
    GitHub solo se e' cambiato. In un thread a parte: un errore qui non deve mai bloccare le modifiche al sito."""
    def _run():
        try:
            import catalogo
            cat = catalogo.build_catalog(os.environ["WC_SITE_URL"])
            if catalogo.push_catalog(cat, GITHUB_REPO, GITHUB_BRANCH, github_headers()):
                logger.info(f"Catalogo prodotti aggiornato su GitHub ({cat['count']} prodotti).")
        except Exception:
            logger.exception("Aggiornamento del catalogo prodotti non riuscito (ritento alla prossima modifica)")
    threading.Thread(target=_run, daemon=True).start()


def process_site_edit(payload: dict) -> bool:
    """True = richiesta chiusa (ok o rifiutata); False = errore temporaneo, da ritentare.
    Serve il login: richiesta firmata dall'utente + sessione attiva."""
    import site_editor as se

    STATS["requests"] += 1
    data = payload.get("site_edit") or {}
    uid = authenticate(payload, data, "Sito")
    if not uid:
        return True
    name = user_name(uid)
    if not require_session(uid, "Sito", payload.get("_queued_at")):
        return True
    kind = data.get("kind")
    kl = {"page": "pagina", "post": "articolo", "product": "prodotto"}.get(kind, str(kind))
    what = describe_request(data) if data.get("action") != "restore" else ["Chiede di tornare alla versione precedente."]
    notify(f"📥 {name} ha inviato una modifica del SITO — elaboro…\n📄 {kl} #{data.get('id')}\n🕒 {fmt_t(time.time())}\n"
           + "\n".join("• " + w for w in what[:20]))
    wp = se.WP(os.environ["WC_SITE_URL"], WP_USERNAME_HOME, WP_APP_PASSWORD_HOME)
    try:
        with _home_lock:
            if data.get("action") == "restore":
                r = se.restore_previous(wp, SITE_BACKUP_DIR, data.get("kind"), data.get("id"))
                STATS["published"] += 1
                record_event({"action": f"sito ripristinato {r['kind']} {r['id']}"})
                if r["kind"] == "product":
                    refresh_catalog_async()
                counter = record_session_action(uid, f"ripristino {kl} «{r.get('title')}»", payload.get("_queued_at"))
                notify(f"↩️ SITO RIPRISTINATO — 👤 {name}\n📄 {kl} «{r.get('title')}» (#{r['id']}) tornato alla versione precedente.\n🔢 {counter}")
                _mark_seen(payload)
                return True
            r = se.publish_edit(wp, SITE_BACKUP_DIR, data, allow_prices=True)
    except se.EditError as e:
        STATS["rejected"] += 1
        logger.warning(f"Sito: richiesta non eseguita: {e}")
        notify(f"⚠️ SITO: modifica di {name} NON eseguita — {e}\n📄 {kl} #{data.get('id')}\nIl sito e' rimasto com'era.")
        _mark_seen(payload)
        return True
    except requests.RequestException as e:
        STATS["errors"] += 1
        logger.exception("Sito: errore di rete/WordPress")
        notify(f"⚠️ SITO: errore temporaneo ({e}) per la modifica di {name} — ritento automaticamente.")
        return False

    _mark_seen(payload)
    if not r["changed"]:
        notify(f"ℹ️ SITO: nessuna differenza rispetto a quanto gia' pubblicato ({name}, {kl} #{data.get('id')}), non ho cambiato nulla.")
        return True
    STATS["published"] += 1
    record_event({"action": f"sito aggiornato {r['kind']} {r['id']}"})
    if r["kind"] == "product":
        refresh_catalog_async()
    n_t = len(data.get("texts") or []) + (1 if data.get("title") else 0)
    n_i = len(data.get("images") or [])
    counter = record_session_action(uid, f"{kl} «{r.get('title')}»: {n_t} testi, {n_i} foto" + (", prezzo" if data.get("prices") else ""), payload.get("_queued_at"))
    lines = "\n".join("• " + x for x in r["summary"][:40])
    notify(
        f"✅ SITO AGGIORNATO — 👤 {name}\n📄 {kl} «{r.get('title')}» (#{r['id']})\n🕒 {fmt_t(time.time())}\n{lines}\n"
        f"Controllo pagina pubblica: {'OK' if r.get('public_ok') else 'DA VERIFICARE'}\n{r.get('link')}\n"
        f"Backup: {Path(r['backup']).name}\n🔢 {counter}\n"
        "Per tornare indietro: «Torna alla versione di prima» nella pagina di modifica."
    )
    return True


def github_headers():
    return {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }


def list_queue_files():
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/queue"
    r = requests.get(url, headers=github_headers(), params={"ref": GITHUB_BRANCH}, timeout=20)
    if r.status_code == 404:
        return []
    r.raise_for_status()
    return [f for f in r.json() if f["name"].endswith(".json")]


def delete_queue_file(path: str, sha: str) -> bool:
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
    r = requests.delete(
        url,
        headers=github_headers(),
        json={"message": f"queue: elaborato {path}", "sha": sha, "branch": GITHUB_BRANCH},
        timeout=20,
    )
    if not r.ok:
        logger.error(f"Cancellazione da coda fallita per {path}: HTTP {r.status_code} {r.text[:300]}")
    return r.ok


# Una foto la cui pubblicazione fallisce (WordPress irraggiungibile, errore
# temporaneo, ecc.) NON deve sparire dalla coda al primo tentativo: resta li'
# e viene ritentata ai prossimi cicli di polling. Per evitare pero' di
# ritentare all'infinito una voce rotta in modo permanente (es. dati
# corrotti), ogni voce ha un numero massimo di tentativi; dopo di che viene
# rimossa comunque, ma con una notifica chiara che serve ricaricare la foto
# a mano, invece di sparire in silenzio (bug corretto il 2026-09-11: prima
# la voce veniva sempre cancellata dopo un solo tentativo, riuscito o no).
QUEUE_MAX_ATTEMPTS = 5
QUEUE_RETRY_STATE_PATH = ROOT / "status" / "queue_retry_state.json"


def _register_queue_failure(filename: str) -> int:
    state = load_json(QUEUE_RETRY_STATE_PATH, {})
    state[filename] = state.get(filename, 0) + 1
    save_json(QUEUE_RETRY_STATE_PATH, state)
    return state[filename]


def _clear_queue_failure(filename: str) -> None:
    state = load_json(QUEUE_RETRY_STATE_PATH, {})
    if filename in state:
        del state[filename]
        save_json(QUEUE_RETRY_STATE_PATH, state)


# Notifica una tantum all'avvio se la coda contiene gia' delle voci: succede
# quando il bot era spento/irraggiungibile mentre la cliente caricava foto
# (il Worker Cloudflare scrive comunque in coda su GitHub, indipendentemente
# dal bot: nulla si perde, ma l'utente non lo sa finche' non gliel'lo diciamo
# esplicitamente invece di lasciare che se ne accorga dalle notifiche singole
# sparse). Si azzera automaticamente ad ogni riavvio del processo.
# --- "Tunnel" dei dati dell'editor attraverso il Worker Cloudflare ------------
# Il Worker inoltra in coda SOLO category, product_id, status, image_base64 e image_mime (tutti gli altri campi li scarta:
# verificato dai file della coda su GitHub il 13/10/2026). Le pagine dell'editor mettono quindi l'INTERA richiesta firmata (JSON)
# dentro image_base64 con image_mime = TUNNEL_MIME; qui la si riapre prima di smistarla. La firma e' dentro la richiesta: la sicurezza non cambia.
TUNNEL_MIME = "application/x-mnp+json"
EDITOR_CATEGORIES = {"session", "home"}      # categorie riservate all'editor: non sono mai prodotti


def queue_epoch(name: str):
    """Orario (secondi) in cui il Worker ha messo la richiesta in coda, ricavato dal nome del file (..._<millisecondi>.json)."""
    m = re.search(r"_(\d{12,14})\.json$", name or "")
    return int(m.group(1)) / 1000 if m else None


def unwrap_tunnel(payload: dict):
    """Ritorna (richiesta_vera, errore). Se non e' un tunnel ritorna (payload, None)."""
    if payload.get("image_mime") != TUNNEL_MIME:
        return payload, None
    try:
        inner = json.loads(base64.b64decode(payload["image_base64"]).decode("utf-8"))
        if not isinstance(inner, dict):
            raise ValueError("non e' un oggetto")
        return inner, None
    except Exception as e:
        return payload, f"dati illeggibili ({str(e)[:60]})"


class PollHealth:
    """Tiene d'occhio i fallimenti di rete verso GitHub: pausa crescente fra i tentativi, UN solo avviso dopo ALERT_AFTER secondi di
    fallimenti continui e UN messaggio quando la rete torna, invece di un avviso nel log ogni 10 secondi."""
    ALERT_AFTER = 600

    def __init__(self, base=10, cap=60, clock=time.time):
        self.base, self.cap, self.clock = base, cap, clock
        self.fails, self.since, self.alerted = 0, None, False

    def failure(self):
        """Ritorna (secondi_di_pausa, manda_avviso, scrivi_nel_log)."""
        now = self.clock()
        if self.since is None:
            self.since = now
        self.fails += 1
        pause = min(self.cap, self.base * (2 ** min(self.fails - 1, 3)))      # 10, 20, 40, 60, 60...
        alert = (not self.alerted) and now - self.since >= self.ALERT_AFTER
        if alert:
            self.alerted = True
        return pause, alert, (self.fails <= 3 or self.fails % 20 == 0)

    def success(self):
        """Ritorna i minuti di interruzione se era stato mandato l'avviso 'giu'', altrimenti None."""
        down = int((self.clock() - self.since) / 60) if (self.alerted and self.since is not None) else None
        self.fails, self.since, self.alerted = 0, None, False
        return down


_startup_backlog_notified = False
_startup_backlog_remaining = 0


def github_queue_polling_loop():
    global _startup_backlog_notified, _startup_backlog_remaining
    if not GITHUB_TOKEN:
        logger.error("GITHUB_TOKEN non impostato in .env, polling della coda GitHub non avviato.")
        return
    logger.info("Polling della coda GitHub avviato per My Nails Live.")
    health = PollHealth(base=GITHUB_POLL_SECONDS)
    while True:
        pause = GITHUB_POLL_SECONDS
        try:
            queue_files = sorted(list_queue_files(), key=lambda q: (queue_epoch(q["name"]) or 0, q["name"]))
            recovered = health.success()
            if recovered is not None:
                notify(f"✅ My Nails: la rete verso GitHub e' tornata dopo circa {recovered} minuti. Riprendo a leggere la coda: le richieste arrivate nel frattempo vengono elaborate ora.")

            if not _startup_backlog_notified:
                _startup_backlog_notified = True
                if queue_files:
                    _startup_backlog_remaining = len(queue_files)
                    logger.info(f"Trovate {len(queue_files)} voci in coda all'avvio (arretrato).")
                    notify(
                        f"📥 My Nails: trovate {len(queue_files)} foto/scelte in coda in attesa "
                        f"(probabilmente arrivate mentre il bot era spento o non raggiungibile). "
                        f"Nessuna e' andata persa: le elaboro ora una alla volta."
                    )

            if queue_files:
                _queue_busy.set()
            for f in queue_files:
                try:
                    # Usiamo l'API "git blobs" (non "contents") per leggere il file:
                    # l'API contents tronca il campo "content" (vuoto, encoding "none")
                    # per file oltre ~1MB, causando un JSONDecodeError su stringa vuota
                    # quando la coda contiene una foto in base64 di dimensioni maggiori.
                    blob_url = f"https://api.github.com/repos/{GITHUB_REPO}/git/blobs/{f['sha']}"
                    file_resp = requests.get(blob_url, headers=github_headers(), timeout=20)
                    file_resp.raise_for_status()
                    content = base64.b64decode(file_resp.json()["content"])
                    payload = json.loads(content)

                    payload, tunnel_err = unwrap_tunnel(payload)
                    if isinstance(payload, dict):
                        payload["_queued_at"] = queue_epoch(f["name"])
                    if tunnel_err:
                        logger.warning(f"Voce di coda {f['name']}: {tunnel_err}")
                        notify(f"⚠️ My Nails: una richiesta dell'editor non si e' potuta leggere ({tunnel_err}). Scartata: va rifatta dalla pagina.")
                        delete_queue_file(f["path"], f["sha"])
                        _clear_queue_failure(f["name"])
                        continue
                    if payload.get("category") in EDITOR_CATEGORIES and not any(payload.get(k) is not None for k in ("session_event", "home_hero", "site_edit")):
                        logger.warning(f"Voce di coda {f['name']}: richiesta dell'editor priva dei dati utili (scartati dal Worker?), ignorata.")
                        notify("⚠️ My Nails: e' arrivata una richiesta dell'editor SENZA i dati utili (il Worker li ha scartati). "
                               "Ricarica la pagina con Ctrl+F5 e riprova; se persiste avvisa Marco. Nessuna modifica fatta.")
                        delete_queue_file(f["path"], f["sha"])
                        _clear_queue_failure(f["name"])
                        continue

                    if payload.get("session_event") is not None:
                        # login / logout / tentativo fallito: sempre chiusi subito (nessun ritento)
                        try:
                            process_session_event(payload)
                        except Exception:
                            logger.exception("Errore imprevisto in un evento di sessione (scartato)")
                        delete_queue_file(f["path"], f["sha"])
                        _clear_queue_failure(f["name"])
                        continue

                    if payload.get("site_edit") is not None:
                        try:
                            success = process_site_edit(payload)
                        except Exception:
                            logger.exception("Errore imprevisto nella richiesta (sito)")
                            notify("⚠️ SITO: errore imprevisto nell'elaborazione di una richiesta (dettagli nel log del telefono). Ritento; dopo 5 tentativi rinuncio.")
                            success = False
                        if success:
                            delete_queue_file(f["path"], f["sha"])
                            _clear_queue_failure(f["name"])
                        else:
                            attempts = _register_queue_failure(f["name"])
                            if attempts >= QUEUE_MAX_ATTEMPTS:
                                notify(f"❌ My Nails Sito: modifica fallita {attempts} volte, rinuncio. Controlla il sito e riprova.")
                                delete_queue_file(f["path"], f["sha"])
                                _clear_queue_failure(f["name"])
                        continue

                    if payload.get("home_hero") is not None:
                        try:
                            success = process_home_hero(payload)
                        except Exception:
                            logger.exception("Errore imprevisto nella richiesta (home)")
                            notify("⚠️ HOME: errore imprevisto nell'elaborazione di una richiesta (dettagli nel log del telefono). Ritento; dopo 5 tentativi rinuncio.")
                            success = False
                        if success:
                            delete_queue_file(f["path"], f["sha"])
                            _clear_queue_failure(f["name"])
                        else:
                            attempts = _register_queue_failure(f["name"])
                            if attempts >= QUEUE_MAX_ATTEMPTS:
                                notify(f"❌ My Nails Home: modifica fallita {attempts} volte, rinuncio. Controlla il sito e riprova.")
                                delete_queue_file(f["path"], f["sha"])
                                _clear_queue_failure(f["name"])
                        continue

                    category = payload.get("category")
                    product_id = str(payload.get("product_id"))
                    status = payload.get("status")
                    image_bytes = None
                    if payload.get("image_base64"):
                        image_bytes = base64.b64decode(payload["image_base64"])

                    photo_uid = ""
                    if category and product_id and status in ("no_change", "custom"):
                        # foto prodotto: se la richiesta porta un utente la firma deve essere valida; con REQUIRE_LOGIN_FOTO=1 il login e' obbligatorio
                        if payload.get("user") or REQUIRE_LOGIN_FOTO:
                            signed_obj = {"category": category, "product_id": payload.get("product_id"), "status": status}
                            photo_uid = authenticate(payload, signed_obj, "Foto prodotto") if payload.get("user") else None
                            if not photo_uid or (REQUIRE_LOGIN_FOTO and not require_session(photo_uid, "Foto prodotto", payload.get("_queued_at"))):
                                if not payload.get("user"):
                                    notify(f"🚫 Foto prodotto RIFIUTATA: serve il login.\n🕒 {fmt_t(time.time())}\nProdotto {product_id} ({category}).")
                                _mark_seen(payload)
                                delete_queue_file(f["path"], f["sha"])
                                _clear_queue_failure(f["name"])
                                continue
                        success = process_choice(category, product_id, status, image_bytes, payload.get("image_mime", ""), photo_uid or "", payload.get("_queued_at"))
                        if success:
                            _mark_seen(payload)
                    else:
                        logger.warning(f"Voce di coda non valida, ignorata: {f['name']}")
                        success = True

                    if success:
                        delete_queue_file(f["path"], f["sha"])
                        _clear_queue_failure(f["name"])
                        if _startup_backlog_remaining > 0:
                            _startup_backlog_remaining -= 1
                            if _startup_backlog_remaining == 0:
                                notify("✅ My Nails: arretrato della coda smaltito, tutto elaborato.")
                    else:
                        attempts = _register_queue_failure(f["name"])
                        if attempts >= QUEUE_MAX_ATTEMPTS:
                            logger.error(f"Voce di coda {f['name']} fallita {attempts} volte, rinuncio e la rimuovo.")
                            notify(
                                f"❌ My Nails: pubblicazione fallita {attempts} volte per {f['name']}, "
                                f"rinuncio ai tentativi automatici — la cliente deve ricaricare la foto manualmente."
                            )
                            delete_queue_file(f["path"], f["sha"])
                            _clear_queue_failure(f["name"])
                        else:
                            logger.warning(f"Voce di coda {f['name']} non riuscita (tentativo {attempts}/{QUEUE_MAX_ATTEMPTS}), la ritento al prossimo ciclo.")
                except Exception:
                    logger.exception(f"Errore elaborando {f.get('name')}")
        except Exception as e:
            pause, alert, verbose = health.failure()
            if verbose:
                logger.warning(f"Errore nel polling della coda GitHub (fallimento n. {health.fails}), riprovo tra {pause}s: {e}")
            if alert:
                notify("⚠️ My Nails: da oltre 10 minuti non riesco a contattare GitHub (rete o DNS del telefono: controlla Wi-Fi/VPN). "
                       "Le richieste di Miriam restano in coda e verranno elaborate appena la rete torna.")
        finally:
            _queue_busy.clear()
        time.sleep(pause)


app = Flask(__name__)


@app.route("/api/health")
def health():
    return jsonify({"ok": True, "uptime_seconds": round(time.time() - START_TIME)})


@app.route("/api/stats")
def stats():
    return jsonify({
        "uptime_seconds": round(time.time() - START_TIME),
        "requests": STATS["requests"],
        "published": STATS["published"],
        "rejected": STATS["rejected"],
        "errors": STATS["errors"],
        "recent_events": STATS["recent_events"],
    })


if __name__ == "__main__":
    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    if not GITHUB_TOKEN:
        logger.warning("GITHUB_TOKEN non impostato in .env: la coda non verra' letta.")
    logger.info(f"Avvio live_server (PID {os.getpid()}), polling coda GitHub + dashboard locale su porta 5001")
    notify("✅ My Nails: server di pubblicazione avviato e in ascolto (coda GitHub, nessun tunnel).")
    threading.Thread(target=github_queue_polling_loop, daemon=True).start()
    threading.Thread(target=session_sweeper_loop, daemon=True).start()
    threading.Thread(target=get_users, daemon=True).start()   # calcola subito le chiavi (PBKDF2): il primo login non aspetta
    if not os.environ.get("EDITOR_USERS"):
        notify("⚠️ My Nails: EDITOR_USERS non impostato nel .env del telefono: nessun utente puo' fare login all'editor del sito.")
    refresh_catalog_async()   # catalogo prodotti per la pagina di Miriam (prezzi, galleria)
    try:
        app.run(host="127.0.0.1", port=5001, debug=False)
    finally:
        PID_FILE.unlink(missing_ok=True)
