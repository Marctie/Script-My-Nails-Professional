"""
Server locale (gira sul tuo PC) che riceve in tempo reale le scelte della
cliente dal sito pubblico GitHub Pages e pubblica SUBITO su WooCommerce:
  - "approved"  -> pubblica la foto elaborata automaticamente
  - "custom"    -> salva la foto caricata da lei e la pubblica
  - "rejected"  -> registra solo il rifiuto (nessuna azione, va rielaborata da te)

La chiave segreta WooCommerce non lascia mai questo PC: il sito pubblico
manda solo "quale prodotto, quale scelta, eventuale foto", mai la chiave.

Protezione: le richieste devono includere l'header X-Review-Token uguale a
REVIEW_API_TOKEN nel tuo .env, per evitare che chiunque trovi il link possa
mandare richieste a vuoto. Non e' una sicurezza bancaria, ma un filtro di base.

Avvio: python app/live_server.py
Poi esponi la porta 5001 (Cloudflare Tunnel o Termux, vedi README) per farla
raggiungere dal sito pubblico.

Monitoraggio (per integrazione con dashboard esterna, es. quella dei bot su Termux):
  - GET  /api/health -> {"ok": true, "uptime_seconds": ...}
  - GET  /api/stats  -> contatori richieste/pubblicazioni/errori ed eventi recenti
  - file di log leggibile in logs/live_server.log (una riga per evento)
  - PID scritto in logs/live_server.pid all'avvio, per script di stop/restart esterni
"""
import base64
import json
import logging
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_cors import CORS

from upload import upload_to_media_library, set_product_image, CONTENT_TYPES  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

REVIEW_API_TOKEN = os.environ.get("REVIEW_API_TOKEN")

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


app = Flask(__name__)
CORS(app)  # il sito su GitHub Pages ha un'origine diversa, serve CORS aperto per questo endpoint


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


def save_custom_image(category: str, product_id: str, data_url: str) -> Path:
    header, b64data = data_url.split(",", 1)
    ext = ".jpg"
    if "image/png" in header:
        ext = ".png"
    elif "image/webp" in header:
        ext = ".webp"
    overrides_dir = processed_dir_for(category) / "client_overrides"
    overrides_dir.mkdir(parents=True, exist_ok=True)
    dest = overrides_dir / f"{product_id}{ext}"
    dest.write_bytes(base64.b64decode(b64data))
    return dest


def publish_image(product_id: int, image_path: Path):
    media_id = upload_to_media_library(image_path)
    set_product_image(product_id, media_id)
    return media_id


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


@app.route("/api/submit", methods=["POST"])
def submit():
    STATS["requests"] += 1

    if REVIEW_API_TOKEN and request.headers.get("X-Review-Token") != REVIEW_API_TOKEN:
        logger.warning("Richiesta rifiutata: token non valido")
        return jsonify({"ok": False, "error": "token non valido"}), 401

    data = request.get_json(force=True)
    category = data.get("category")
    product_id = str(data.get("product_id"))
    status = data.get("status")
    custom_image = data.get("customImage")

    if not category or not product_id or status not in ("no_change", "custom"):
        STATS["errors"] += 1
        return jsonify({"ok": False, "error": "dati mancanti o non validi"}), 400

    review_state_path = processed_dir_for(category) / "review_state.json"
    review_state = load_json(review_state_path, {})
    review_state[product_id] = status
    save_json(review_state_path, review_state)

    entry = find_manifest_entry(category, product_id)
    if entry is None:
        STATS["errors"] += 1
        logger.error(f"Prodotto {product_id} non trovato in categoria {category}")
        return jsonify({"ok": False, "error": f"prodotto {product_id} non trovato in categoria {category}"}), 404

    log_path = processed_dir_for(category) / "live_publish_log.json"
    log = load_json(log_path, {})

    try:
        if status == "custom" and custom_image:
            image_path = save_custom_image(category, product_id, custom_image)
            media_id = publish_image(entry["product_id"], image_path)
            log[product_id] = {"status": "pubblicato", "media_id": media_id, "source": "foto cliente"}
            save_json(log_path, log)
            STATS["published"] += 1
            record_event({"category": category, "product_id": product_id, "action": "pubblicato (foto cliente)"})
            logger.info(f"[{category}] {product_id} pubblicato (foto cliente), media_id={media_id}")
            notify_command_center(f"🖼️ My Nails: prodotto {product_id} ({category}) pubblicato su WooCommerce (foto cliente).")
            return jsonify({"ok": True, "published": True, "media_id": media_id})

        else:
            # no_change: la cliente ha detto che la foto attuale va bene, solo registrato
            log[product_id] = {"status": "registrato, nessuna pubblicazione", "review": status}
            save_json(log_path, log)
            STATS["rejected"] += 1
            record_event({"category": category, "product_id": product_id, "action": f"registrato ({status})"})
            logger.info(f"[{category}] {product_id} registrato senza pubblicazione (status={status})")
            return jsonify({"ok": True, "published": False})

    except Exception as e:
        log[product_id] = {"status": f"errore: {e}"}
        save_json(log_path, log)
        STATS["errors"] += 1
        record_event({"category": category, "product_id": product_id, "action": f"errore: {e}"})
        logger.exception(f"[{category}] {product_id} errore durante la pubblicazione")
        notify_command_center(f"⚠️ My Nails: errore pubblicando il prodotto {product_id} ({category}): {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


if __name__ == "__main__":
    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    if not REVIEW_API_TOKEN:
        logger.warning("REVIEW_API_TOKEN non impostato in .env, l'endpoint sara' aperto a chiunque conosca l'URL.")
    logger.info(f"Avvio live_server su porta 5001 (PID {os.getpid()})")
    notify_command_center("✅ My Nails: server di pubblicazione avviato e in ascolto.")
    try:
        app.run(host="0.0.0.0", port=5001, debug=False)
    finally:
        PID_FILE.unlink(missing_ok=True)
