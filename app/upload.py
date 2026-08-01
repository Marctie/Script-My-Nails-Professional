"""
Carica su WooCommerce SOLO le immagini elaborate che sono state marcate come
"approved" nella dashboard di revisione (processed/review_state.json).
Ogni immagine viene caricata nella Media Library di WordPress e poi impostata
come immagine principale del prodotto corrispondente.

Uso:
  python app/upload.py            # carica tutte le approvate non ancora caricate
  python app/upload.py --dry-run  # mostra solo cosa farebbe, senza caricare nulla
"""
import base64
import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv
from woocommerce import API

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

SITE_URL = os.environ["WC_SITE_URL"].rstrip("/")
CONSUMER_KEY = os.environ["WC_CONSUMER_KEY"]
CONSUMER_SECRET = os.environ["WC_CONSUMER_SECRET"]

PROCESS_MANIFEST = ROOT / "processed" / "process_manifest.json"
REVIEW_STATE_PATH = ROOT / "processed" / "review_state.json"
UPLOAD_LOG_PATH = ROOT / "processed" / "upload_log.json"

wcapi = API(
    url=SITE_URL,
    consumer_key=CONSUMER_KEY,
    consumer_secret=CONSUMER_SECRET,
    version="wc/v3",
    timeout=30,
)


def upload_to_media_library(image_path: Path) -> int:
    """Carica il file su WP Media Library via REST API, ritorna l'id media."""
    media_endpoint = f"{SITE_URL}/wp-json/wp/v2/media"
    with open(image_path, "rb") as f:
        file_bytes = f.read()

    headers = {
        "Content-Disposition": f'attachment; filename="{image_path.name}"',
        "Content-Type": "image/png",
    }
    auth = (CONSUMER_KEY, CONSUMER_SECRET)
    resp = requests.post(media_endpoint, headers=headers, data=file_bytes, auth=auth, timeout=60)
    resp.raise_for_status()
    return resp.json()["id"]


def set_product_image(product_id: int, media_id: int):
    resp = wcapi.put(f"products/{product_id}", {"images": [{"id": media_id}]})
    resp.raise_for_status()
    return resp.json()


def main():
    dry_run = "--dry-run" in sys.argv

    manifest = json.loads(PROCESS_MANIFEST.read_text(encoding="utf-8"))
    review_state = json.loads(REVIEW_STATE_PATH.read_text(encoding="utf-8")) if REVIEW_STATE_PATH.exists() else {}
    upload_log = json.loads(UPLOAD_LOG_PATH.read_text(encoding="utf-8")) if UPLOAD_LOG_PATH.exists() else {}

    approved = [
        e for e in manifest
        if e.get("process_status") == "ok"
        and review_state.get(str(e["product_id"])) == "approved"
        and str(e["product_id"]) not in upload_log
    ]

    if not approved:
        print("Nessuna immagine approvata da caricare (o gia' tutte caricate in precedenza).")
        return

    print(f"Da caricare: {len(approved)} prodotti.")
    for entry in approved:
        pid = entry["product_id"]
        name = entry["name"]
        image_path = ROOT / entry["processed_path"]

        if dry_run:
            print(f"  [DRY-RUN] Caricherei {image_path.name} su prodotto {pid} ({name})")
            continue

        try:
            media_id = upload_to_media_library(image_path)
            set_product_image(pid, media_id)
            upload_log[str(pid)] = {"media_id": media_id, "image": str(image_path.name), "status": "ok"}
            print(f"  [ok] {pid} - {name} -> media_id={media_id}")
        except Exception as e:
            upload_log[str(pid)] = {"status": f"errore: {e}"}
            print(f"  [ERRORE] {pid} - {name}: {e}")

        UPLOAD_LOG_PATH.write_text(json.dumps(upload_log, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\nFatto. Log upload:", UPLOAD_LOG_PATH)


if __name__ == "__main__":
    main()
