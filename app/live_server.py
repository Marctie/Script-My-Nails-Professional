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


def publish_image(category: str, product_id: int, image_path: Path):
    backup_live_image(category, product_id)
    media_id = upload_to_media_library(image_path)
    set_product_image(product_id, media_id)
    return media_id


def process_choice(category: str, product_id: str, status: str, image_bytes: bytes = None, image_mime: str = ""):
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
        return

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
    except Exception as e:
        log[product_id] = {"status": f"errore: {e}"}
        save_json(log_path, log)
        STATS["errors"] += 1
        record_event({"category": category, "product_id": product_id, "action": f"errore: {e}"})
        logger.exception(f"[{category}] {product_id} errore durante la pubblicazione")
        notify(f"❌ My Nails: pubblicazione FALLITA per \"{product_name}\" ({category}), prodotto {product_id}: {e}")


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


def delete_queue_file(path: str, sha: str):
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
    requests.delete(
        url,
        headers=github_headers(),
        json={"message": f"queue: elaborato {path}", "sha": sha, "branch": GITHUB_BRANCH},
        timeout=20,
    )


def github_queue_polling_loop():
    if not GITHUB_TOKEN:
        logger.error("GITHUB_TOKEN non impostato in .env, polling della coda GitHub non avviato.")
        return
    logger.info("Polling della coda GitHub avviato per My Nails Live.")
    while True:
        try:
            for f in list_queue_files():
                try:
                    file_resp = requests.get(f["url"], headers=github_headers(), timeout=20)
                    file_resp.raise_for_status()
                    content = base64.b64decode(file_resp.json()["content"])
                    payload = json.loads(content)

                    category = payload.get("category")
                    product_id = str(payload.get("product_id"))
                    status = payload.get("status")
                    image_bytes = None
                    if payload.get("image_base64"):
                        image_bytes = base64.b64decode(payload["image_base64"])

                    if category and product_id and status in ("no_change", "custom"):
                        process_choice(category, product_id, status, image_bytes, payload.get("image_mime", ""))
                    else:
                        logger.warning(f"Voce di coda non valida, ignorata: {f['name']}")

                    delete_queue_file(f["path"], f["sha"])
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
    try:
        app.run(host="127.0.0.1", port=5001, debug=False)
    finally:
        PID_FILE.unlink(missing_ok=True)
