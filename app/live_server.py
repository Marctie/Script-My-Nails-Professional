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


def process_choice(category: str, product_id: str, status: str, image_bytes: bytes = None, image_mime: str = "") -> bool:
    """Ritorna True se la voce puo' essere considerata definita (pubblicata
    con successo, o registrata senza bisogno di pubblicare) e quindi rimossa
    dalla coda; False se la pubblicazione e' fallita e andrebbe ritentata
    (vedi QUEUE_MAX_ATTEMPTS in github_queue_polling_loop) prima di darla
    per persa."""
    STATS["requests"] += 1

    review_state_path = processed_dir_for(category) / "review_state.json"
    review_state = load_json(review_state_path, {})
    review_state[product_id] = status
    save_json(review_state_path, review_state)

    entry = find_manifest_entry(category, product_id)
    if entry is None:
        STATS["errors"] += 1
        logger.error(f"Prodotto {product_id} non trovato in categoria {category}")
        notify(f"⚠️ My Nails: prodotto {product_id} non trovato in categoria {category}, scelta ignorata.")
        return True  # non c'e' nulla da ritentare: il prodotto non esiste nel manifest

    log_path = processed_dir_for(category) / "live_publish_log.json"
    log = load_json(log_path, {})
    product_name = entry.get("name", product_id)

    try:
        if status == "custom" and image_bytes:
            notify(f"📩 My Nails: ricevuta una foto dalla cliente per \"{product_name}\" ({category}), avvio pubblicazione...")
            image_path = save_custom_image_bytes(category, product_id, image_bytes, image_mime)
            notify(f"💾 My Nails: foto della cliente salvata in locale per \"{product_name}\" ({category}).")
            media_id = publish_image(category, entry["product_id"], image_path)
            log[product_id] = {"status": "pubblicato", "media_id": media_id, "source": "foto cliente"}
            save_json(log_path, log)
            STATS["published"] += 1
            record_event({"category": category, "product_id": product_id, "action": "pubblicato (foto cliente)"})
            logger.info(f"[{category}] {product_id} pubblicato (foto cliente), media_id={media_id}")
            notify(
                f"⬆️ My Nails: nuova foto caricata su WordPress (media_id={media_id}) e impostata come "
                f"immagine di \"{product_name}\" ({category}) su mynailsprofessional.it. ✅ Pubblicato."
            )
        else:
            log[product_id] = {"status": "registrato, nessuna pubblicazione", "review": status}
            save_json(log_path, log)
            STATS["rejected"] += 1
            record_event({"category": category, "product_id": product_id, "action": f"registrato ({status})"})
            logger.info(f"[{category}] {product_id} registrato senza pubblicazione (status={status})")
            notify(f"✔️ My Nails: \"{product_name}\" ({category}) confermato dalla cliente, nessuna modifica alla foto.")
        return True
    except Exception as e:
        STATS["errors"] += 1
        record_event({"category": category, "product_id": product_id, "action": f"errore: {e}"})
        logger.exception(f"[{category}] {product_id} errore durante la pubblicazione")
        notify(f"⚠️ My Nails: pubblicazione fallita per \"{product_name}\" ({category}), prodotto {product_id}: {e} — ritento automaticamente.")
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
    """True = richiesta chiusa (ok o rifiutata); False = errore temporaneo, da ritentare."""
    import home_editor as he

    STATS["requests"] += 1
    data = payload.get("home_hero") or {}
    action = data.get("action", "publish")
    if HOME_EDITOR_PIN and not he.verify_signature(HOME_EDITOR_PIN, payload, seen=_home_seen_sigs):
        STATS["rejected"] += 1
        logger.warning("Richiesta Home rifiutata: firma mancante, errata, scaduta o gia' usata.")
        notify("🚫 My Nails Home: richiesta di modifica RIFIUTATA (codice errato, scaduto o gia' usato). Nessuna modifica fatta.")
        return True
    wp = he.WP(os.environ["WC_SITE_URL"], WP_USERNAME_HOME, WP_APP_PASSWORD_HOME)
    try:
        with _home_lock:
            if action == "restore":
                r = he.restore_previous(wp, HOME_BACKUP_DIR)
                STATS["published"] += 1
                record_event({"action": "home ripristinata"})
                notify(f"↩️ My Nails Home: ripristinata la versione precedente ({r['restored_from']}).\nOra: {r['after'].get('title')} | {r['after'].get('button')}")
                return True
            image_bytes = base64.b64decode(data["image_base64"]) if data.get("image_base64") else None
            r = he.publish_hero(wp, HOME_BACKUP_DIR, data, image_bytes)
    except he.HeroError as e:
        STATS["rejected"] += 1
        logger.warning(f"Home: richiesta non eseguita: {e}")
        notify(f"⚠️ My Nails Home: modifica NON eseguita — {e}\nIl sito e' rimasto com'era.")
        return True
    except requests.RequestException as e:
        STATS["errors"] += 1
        logger.exception("Home: errore di rete/WordPress")
        notify(f"⚠️ My Nails Home: errore temporaneo ({e}) — ritento automaticamente.")
        return False

    if not r["changed"]:
        notify("ℹ️ My Nails Home: nessuna differenza rispetto a quanto gia' pubblicato, non ho cambiato nulla.")
        return True
    STATS["published"] += 1
    record_event({"action": "home aggiornata", "revision": r.get("revision")})
    b, a = r["before"], r["after"]
    notify(
        "✅ My Nails Home AGGIORNATA da Miriam\n"
        f"PRIMA: {b.get('title')} | {b.get('button')}\nDOPO:  {a.get('title')} | {a.get('button')}\n"
        f"Foto: {'NUOVA ' + r['image_url'] if r.get('image_url') else 'invariata'}\n"
        f"Controllo pagina pubblica: {'OK' if r.get('public_ok') else 'DA VERIFICARE'}\n"
        f"Revisione WordPress: {r.get('revision')}\nBackup: {Path(r['backup']).name}\n"
        "Per tornare indietro: pulsante «Torna alla versione di prima» nella pagina di Miriam."
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
    """True = richiesta chiusa (ok o rifiutata); False = errore temporaneo, da ritentare."""
    import site_editor as se

    STATS["requests"] += 1
    data = payload.get("site_edit") or {}
    if not HOME_EDITOR_PIN:
        STATS["rejected"] += 1
        notify("🚫 My Nails Sito: richiesta rifiutata, manca HOME_EDITOR_PIN nel .env del telefono. Nessuna modifica fatta.")
        return True
    if not se.verify_signature(HOME_EDITOR_PIN, payload, key="site_edit", seen=_home_seen_sigs):
        STATS["rejected"] += 1
        logger.warning("Richiesta Sito rifiutata: firma mancante, errata, scaduta o gia' usata.")
        notify("🚫 My Nails Sito: richiesta di modifica RIFIUTATA (codice errato, scaduto o gia' usato). Nessuna modifica fatta.")
        return True
    wp = se.WP(os.environ["WC_SITE_URL"], WP_USERNAME_HOME, WP_APP_PASSWORD_HOME)
    try:
        with _home_lock:
            if data.get("action") == "restore":
                r = se.restore_previous(wp, SITE_BACKUP_DIR, data.get("kind"), data.get("id"))
                STATS["published"] += 1
                record_event({"action": f"sito ripristinato {r['kind']} {r['id']}"})
                if r["kind"] == "product":
                    refresh_catalog_async()
                notify(f"↩️ My Nails Sito: ripristinata la versione precedente di «{r.get('title')}» ({r['kind']} {r['id']}).")
                return True
            r = se.publish_edit(wp, SITE_BACKUP_DIR, data, allow_prices=True)
    except se.EditError as e:
        STATS["rejected"] += 1
        logger.warning(f"Sito: richiesta non eseguita: {e}")
        notify(f"⚠️ My Nails Sito: modifica NON eseguita — {e}\nIl sito e' rimasto com'era.")
        return True
    except requests.RequestException as e:
        STATS["errors"] += 1
        logger.exception("Sito: errore di rete/WordPress")
        notify(f"⚠️ My Nails Sito: errore temporaneo ({e}) — ritento automaticamente.")
        return False

    if not r["changed"]:
        notify("ℹ️ My Nails Sito: nessuna differenza rispetto a quanto gia' pubblicato, non ho cambiato nulla.")
        return True
    STATS["published"] += 1
    record_event({"action": f"sito aggiornato {r['kind']} {r['id']}"})
    if r["kind"] == "product":
        refresh_catalog_async()
    lines = "\n".join("• " + x for x in r["summary"][:15])
    notify(
        f"✅ My Nails SITO AGGIORNATO da Miriam: «{r.get('title')}» ({r['kind']} {r['id']})\n{lines}\n"
        f"Controllo pagina pubblica: {'OK' if r.get('public_ok') else 'DA VERIFICARE'}\n{r.get('link')}\n"
        f"Backup: {Path(r['backup']).name}\n"
        "Per tornare indietro: pulsante «Torna alla versione di prima» nella pagina di Miriam."
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
_startup_backlog_notified = False
_startup_backlog_remaining = 0


def github_queue_polling_loop():
    global _startup_backlog_notified, _startup_backlog_remaining
    if not GITHUB_TOKEN:
        logger.error("GITHUB_TOKEN non impostato in .env, polling della coda GitHub non avviato.")
        return
    logger.info("Polling della coda GitHub avviato per My Nails Live.")
    while True:
        try:
            queue_files = list_queue_files()

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

                    if payload.get("site_edit") is not None:
                        success = process_site_edit(payload)
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
                        success = process_home_hero(payload)
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

                    if category and product_id and status in ("no_change", "custom"):
                        success = process_choice(category, product_id, status, image_bytes, payload.get("image_mime", ""))
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
            logger.warning(f"Errore nel polling della coda GitHub, riprovo tra {GITHUB_POLL_SECONDS}s: {e}")
        time.sleep(GITHUB_POLL_SECONDS)


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
    refresh_catalog_async()   # catalogo prodotti per la pagina di Miriam (prezzi, galleria)
    try:
        app.run(host="127.0.0.1", port=5001, debug=False)
    finally:
        PID_FILE.unlink(missing_ok=True)
