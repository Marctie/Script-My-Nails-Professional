"""
Bot (gira su Termux, tablet sempre acceso, gestito da Termux-Launcher come
gli altri bot) che riceve in tempo reale le scelte della cliente dal sito
pubblico GitHub Pages e pubblica SUBITO su WooCommerce:
  - "custom"    -> salva la foto caricata da lei e la pubblica
  - "no_change" -> registra solo che per ora va bene cosi' (nessuna azione)

Niente server esposto su internet, niente tunnel: il sito statico manda
foto+scelta direttamente all'API pubblica di Telegram (bot dedicato,
TELEGRAM_LIVE_BOT_TOKEN), e questo script le riceve facendo polling
(getUpdates), esattamente come gli altri bot Telegram del progetto. La
chiave WooCommerce non lascia mai questo dispositivo.

Protezione: ogni messaggio deve contenere, nel testo/caption, il
REVIEW_API_TOKEN del tuo .env — i messaggi senza (o con token sbagliato)
vengono ignorati. Non e' una sicurezza bancaria, ma un filtro di base
contro chi trovasse per caso il token del bot (comunque pubblico in
docs/config.js, come nel flusso precedente).

Gestito da Termux-Launcher (voce "nails_live" in bots.conf): avvio/stop/restart
tramite la dashboard su http://127.0.0.1:8765 o con
  bash ~/bots/Termux-Launcher/bot_ctl.sh nails_live start|stop|restart

Monitoraggio (in aggiunta a quello gia' offerto da Termux-Launcher via psutil/tmux):
  - GET  http://127.0.0.1:5001/api/health -> {"ok": true, "uptime_seconds": ...}
  - GET  http://127.0.0.1:5001/api/stats  -> contatori richieste/pubblicazioni/errori
    (solo locale, usato dalla dashboard di Termux-Launcher: NON esposto su internet)
  - file di log leggibile in logs/live_server.log
"""
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

REVIEW_API_TOKEN = os.environ.get("REVIEW_API_TOKEN")
TELEGRAM_LIVE_BOT_TOKEN = os.environ.get("TELEGRAM_LIVE_BOT_TOKEN")
TELEGRAM_LIVE_CHAT_ID = os.environ.get("TELEGRAM_LIVE_CHAT_ID")

LOGS_DIR = ROOT / "logs"
LOGS_DIR.mkdir(exist_ok=True)
LOG_FILE = LOGS_DIR / "live_server.log"
STATUS_DIR = ROOT / "status"
STATUS_DIR.mkdir(exist_ok=True)
OFFSET_FILE = STATUS_DIR / "telegram_offset.txt"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler()],
)
logger = logging.getLogger("live_server")

START_TIME = time.time()
STATS = {"requests": 0, "published": 0, "rejected": 0, "errors": 0, "recent_events": []}
MAX_RECENT_EVENTS = 50

# Notifiche di stato/attivita' tramite il bot Telegram "Centro di Comando"
# gia' in uso per gli altri bot su Termux: si riusa solo il suo token +
# ADMIN_CHAT_IDS (chiamata diretta all'API Telegram), senza bisogno che
# quel bot sia acceso ne' di duplicare credenziali in questo progetto.
# Presuppone che questa cartella sia una sorella di "Termux-Launcher" dentro
# ~/bots/ (stessa struttura usata per tutti gli altri bot).
COMMAND_CENTER_ENV = ROOT.parent / "Termux-Launcher" / "command_center" / ".env"


def notify_command_center(text: str) -> None:
    try:
        if not COMMAND_CENTER_ENV.exists():
            return
        env = {}
        for line in COMMAND_CENTER_ENV.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()

        token = env.get("TELEGRAM_BOT_TOKEN")
        chat_ids = env.get("ADMIN_CHAT_IDS", "")
        if not token:
            return

        for chat_id in filter(None, (c.strip() for c in chat_ids.split(","))):
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data={"chat_id": chat_id, "text": text},
                timeout=5,
            )
    except Exception:
        logger.exception("Impossibile inviare notifica al Centro di Comando")


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


def save_custom_image_bytes(category: str, product_id: str, data: bytes, ext: str) -> Path:
    overrides_dir = processed_dir_for(category) / "client_overrides"
    overrides_dir.mkdir(parents=True, exist_ok=True)
    dest = overrides_dir / f"{product_id}{ext}"
    dest.write_bytes(data)
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
        notify_command_center(f"⬇️ My Nails: scaricata e salvata la foto attuale del prodotto {product_id} ({category}) prima della sostituzione.")
    except Exception as e:
        logger.warning(f"[{category}] backup foto live fallito per {product_id}: {e}")
        notify_command_center(f"⚠️ My Nails: backup della foto attuale FALLITO per il prodotto {product_id} ({category}): {e}")


def publish_image(category: str, product_id: int, image_path: Path):
    backup_live_image(category, product_id)
    media_id = upload_to_media_library(image_path)
    set_product_image(product_id, media_id)
    return media_id


def process_choice(category: str, product_id: str, status: str, image_bytes: bytes | None, image_ext: str):
    """Stessa logica che prima viveva in /api/submit, ora chiamata dal loop
    di polling Telegram invece che da una richiesta HTTP pubblica."""
    STATS["requests"] += 1

    review_state_path = processed_dir_for(category) / "review_state.json"
    review_state = load_json(review_state_path, {})
    review_state[product_id] = status
    save_json(review_state_path, review_state)

    entry = find_manifest_entry(category, product_id)
    if entry is None:
        STATS["errors"] += 1
        logger.error(f"Prodotto {product_id} non trovato in categoria {category}")
        return

    log_path = processed_dir_for(category) / "live_publish_log.json"
    log = load_json(log_path, {})

    product_name = entry.get("name", product_id)

    try:
        if status == "custom" and image_bytes:
            notify_command_center(f"📩 My Nails: ricevuta una foto dalla cliente per \"{product_name}\" ({category}), avvio pubblicazione...")
            image_path = save_custom_image_bytes(category, product_id, image_bytes, image_ext)
            notify_command_center(f"💾 My Nails: foto della cliente salvata in locale per \"{product_name}\" ({category}).")
            media_id = publish_image(category, entry["product_id"], image_path)
            log[product_id] = {"status": "pubblicato", "media_id": media_id, "source": "foto cliente"}
            save_json(log_path, log)
            STATS["published"] += 1
            record_event({"category": category, "product_id": product_id, "action": "pubblicato (foto cliente)"})
            logger.info(f"[{category}] {product_id} pubblicato (foto cliente), media_id={media_id}")
            notify_command_center(
                f"⬆️ My Nails: nuova foto caricata su WordPress (media_id={media_id}) e impostata come "
                f"immagine di \"{product_name}\" ({category}) su mynailsprofessional.it. ✅ Pubblicato."
            )

        else:
            # no_change: la cliente ha detto che la foto attuale va bene, solo registrato
            log[product_id] = {"status": "registrato, nessuna pubblicazione", "review": status}
            save_json(log_path, log)
            STATS["rejected"] += 1
            record_event({"category": category, "product_id": product_id, "action": f"registrato ({status})"})
            logger.info(f"[{category}] {product_id} registrato senza pubblicazione (status={status})")
            notify_command_center(f"✔️ My Nails: \"{product_name}\" ({category}) confermato dalla cliente, nessuna modifica alla foto.")

    except Exception as e:
        log[product_id] = {"status": f"errore: {e}"}
        save_json(log_path, log)
        STATS["errors"] += 1
        record_event({"category": category, "product_id": product_id, "action": f"errore: {e}"})
        logger.exception(f"[{category}] {product_id} errore durante la pubblicazione")
        notify_command_center(f"⚠️ My Nails: errore pubblicando il prodotto {product_id} ({category}): {e}")


def _parse_caption(text: str):
    """Formato atteso, mandato dal sito statico: MYNAILS|<token>|<categoria>|<product_id>|<stato>"""
    parts = (text or "").strip().split("|")
    if len(parts) != 5 or parts[0] != "MYNAILS":
        return None
    _marker, token, category, product_id, status = parts
    if not REVIEW_API_TOKEN or token != REVIEW_API_TOKEN:
        return None
    if status not in ("custom", "no_change"):
        return None
    return category, product_id, status


def _download_telegram_file(file_id: str) -> tuple[bytes, str] | None:
    resp = requests.get(
        f"https://api.telegram.org/bot{TELEGRAM_LIVE_BOT_TOKEN}/getFile",
        params={"file_id": file_id}, timeout=15,
    )
    resp.raise_for_status()
    file_path = resp.json()["result"]["file_path"]
    ext = Path(file_path).suffix or ".jpg"
    file_resp = requests.get(
        f"https://api.telegram.org/file/bot{TELEGRAM_LIVE_BOT_TOKEN}/{file_path}", timeout=30,
    )
    file_resp.raise_for_status()
    return file_resp.content, ext


def _handle_update(update: dict):
    message = update.get("message") or update.get("channel_post")
    if not message:
        return
    if TELEGRAM_LIVE_CHAT_ID and str(message.get("chat", {}).get("id")) != str(TELEGRAM_LIVE_CHAT_ID):
        return

    caption = message.get("caption") or message.get("text") or ""
    parsed = _parse_caption(caption)
    if not parsed:
        return
    category, product_id, status = parsed

    image_bytes, image_ext = None, ".jpg"
    if status == "custom":
        file_id = None
        if message.get("document"):
            file_id = message["document"]["file_id"]
        elif message.get("photo"):
            file_id = message["photo"][-1]["file_id"]  # risoluzione piu' alta disponibile
        if file_id:
            downloaded = _download_telegram_file(file_id)
            if downloaded:
                image_bytes, image_ext = downloaded

    process_choice(category, product_id, status, image_bytes, image_ext)


def _load_offset() -> int:
    try:
        return int(OFFSET_FILE.read_text(encoding="utf-8").strip())
    except Exception:
        return 0


def _save_offset(offset: int) -> None:
    OFFSET_FILE.write_text(str(offset), encoding="utf-8")


def telegram_polling_loop():
    if not TELEGRAM_LIVE_BOT_TOKEN:
        logger.warning("TELEGRAM_LIVE_BOT_TOKEN non impostato in .env: il polling non parte, "
                        "nessuna scelta della cliente verra' mai ricevuta.")
        return

    offset = _load_offset()
    logger.info("Polling Telegram avviato (bot dedicato pubblicazione My Nails).")
    while True:
        try:
            resp = requests.get(
                f"https://api.telegram.org/bot{TELEGRAM_LIVE_BOT_TOKEN}/getUpdates",
                params={"offset": offset, "timeout": 30},
                timeout=40,
            )
            resp.raise_for_status()
            updates = resp.json().get("result", [])
            for update in updates:
                offset = update["update_id"] + 1
                try:
                    _handle_update(update)
                except Exception:
                    logger.exception("Errore gestendo un aggiornamento Telegram")
                _save_offset(offset)
        except Exception:
            logger.exception("Errore nel polling Telegram, riprovo tra 5s")
            time.sleep(5)


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
    logger.info(f"Avvio live_server (PID {os.getpid()})")
    notify_command_center("✅ My Nails: bot di pubblicazione avviato e in ascolto (via Telegram, nessun tunnel).")
    threading.Thread(target=telegram_polling_loop, daemon=True).start()
    # Solo locale: la dashboard di Termux-Launcher legge questi endpoint da 127.0.0.1,
    # non serve (e non va) esporli su internet.
    app.run(host="127.0.0.1", port=5001, debug=False)
