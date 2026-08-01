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
Poi esponi la porta 5001 con Cloudflare Tunnel (vedi README) per farla
raggiungere dal sito pubblico.
"""
import base64
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_cors import CORS

from upload import upload_to_media_library, set_product_image, CONTENT_TYPES  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

REVIEW_API_TOKEN = os.environ.get("REVIEW_API_TOKEN")

app = Flask(__name__)
CORS(app)  # il sito su GitHub Pages ha un'origine diversa, serve CORS aperto per questo endpoint


def processed_dir_for(category: str) -> Path:
    return ROOT / "processed" / category


def load_json(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def find_manifest_entry(category: str, product_id: str):
    manifest = load_json(processed_dir_for(category) / "process_manifest.json", [])
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
    return jsonify({"ok": True})


@app.route("/api/submit", methods=["POST"])
def submit():
    if REVIEW_API_TOKEN and request.headers.get("X-Review-Token") != REVIEW_API_TOKEN:
        return jsonify({"ok": False, "error": "token non valido"}), 401

    data = request.get_json(force=True)
    category = data.get("category")
    product_id = str(data.get("product_id"))
    status = data.get("status")
    custom_image = data.get("customImage")

    if not category or not product_id or status not in ("approved", "rejected", "custom"):
        return jsonify({"ok": False, "error": "dati mancanti o non validi"}), 400

    review_state_path = processed_dir_for(category) / "review_state.json"
    review_state = load_json(review_state_path, {})
    review_state[product_id] = status
    save_json(review_state_path, review_state)

    entry = find_manifest_entry(category, product_id)
    if entry is None:
        return jsonify({"ok": False, "error": f"prodotto {product_id} non trovato in categoria {category}"}), 404

    log_path = processed_dir_for(category) / "live_publish_log.json"
    log = load_json(log_path, {})

    try:
        if status == "approved" and entry.get("process_status") == "ok":
            image_path = ROOT / entry["processed_path"]
            media_id = publish_image(entry["product_id"], image_path)
            log[product_id] = {"status": "pubblicato", "media_id": media_id, "source": "elaborazione automatica"}
            save_json(log_path, log)
            return jsonify({"ok": True, "published": True, "media_id": media_id})

        elif status == "custom" and custom_image:
            image_path = save_custom_image(category, product_id, custom_image)
            media_id = publish_image(entry["product_id"], image_path)
            log[product_id] = {"status": "pubblicato", "media_id": media_id, "source": "foto cliente"}
            save_json(log_path, log)
            return jsonify({"ok": True, "published": True, "media_id": media_id})

        else:
            # rejected, oppure approved senza elaborazione riuscita: solo registrato
            log[product_id] = {"status": "registrato, nessuna pubblicazione", "review": status}
            save_json(log_path, log)
            return jsonify({"ok": True, "published": False})

    except Exception as e:
        log[product_id] = {"status": f"errore: {e}"}
        save_json(log_path, log)
        return jsonify({"ok": False, "error": str(e)}), 500


if __name__ == "__main__":
    if not REVIEW_API_TOKEN:
        print("ATTENZIONE: REVIEW_API_TOKEN non impostato in .env, l'endpoint sara' aperto a chiunque conosca l'URL.")
    app.run(host="0.0.0.0", port=5001, debug=False)
