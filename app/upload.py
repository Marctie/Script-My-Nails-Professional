"""
Carica su WooCommerce SOLO le immagini elaborate che sono state marcate come
"approved" nella dashboard di revisione (processed/review_state.json).
Ogni immagine viene caricata nella Media Library di WordPress e poi impostata
come immagine principale del prodotto corrispondente.

Uso:
  python app/upload.py <categoria> [--dry-run]
  python app/upload.py color-gel acrygel semi-permanente --dry-run
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

wcapi = API(
    url=SITE_URL,
    consumer_key=CONSUMER_KEY,
    consumer_secret=CONSUMER_SECRET,
    version="wc/v3",
    timeout=30,
)


CONTENT_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


def upload_to_media_library(image_path: Path) -> int:
    """Carica il file su WP Media Library via REST API, ritorna l'id media."""
    media_endpoint = f"{SITE_URL}/wp-json/wp/v2/media"
    with open(image_path, "rb") as f:
        file_bytes = f.read()

    content_type = CONTENT_TYPES.get(image_path.suffix.lower(), "image/jpeg")
    headers = {
        "Content-Disposition": f'attachment; filename="{image_path.name}"',
        "Content-Type": content_type,
    }
    auth = (CONSUMER_KEY, CONSUMER_SECRET)
    resp = requests.post(media_endpoint, headers=headers, data=file_bytes, auth=auth, timeout=60)
    resp.raise_for_status()
    return resp.json()["id"]


def set_product_image(product_id: int, media_id: int):
    resp = wcapi.put(f"products/{product_id}", {"images": [{"id": media_id}]})
    resp.raise_for_status()
    return resp.json()


def upload_category(category: str, dry_run: bool):
    processed_dir = ROOT / "processed" / category
    process_manifest_path = processed_dir / "process_manifest.json"
    review_state_path = processed_dir / "review_state.json"
    upload_log_path = processed_dir / "upload_log.json"

    if not process_manifest_path.exists():
        print(f"[{category}] Nessun manifest elaborato trovato, salto (esegui prima process.py).")
        return

    manifest = json.loads(process_manifest_path.read_text(encoding="utf-8"))
    review_state = json.loads(review_state_path.read_text(encoding="utf-8")) if review_state_path.exists() else {}
    upload_log = json.loads(upload_log_path.read_text(encoding="utf-8")) if upload_log_path.exists() else {}
    overrides_dir = processed_dir / "client_overrides"

    to_upload = []
    for e in manifest:
        pid = str(e["product_id"])
        if pid in upload_log:
            continue
        status = review_state.get(pid)
        if status == "approved" and e.get("process_status") == "ok":
            to_upload.append((e, ROOT / e["processed_path"]))
        elif status == "custom":
            override_matches = list(overrides_dir.glob(f"{pid}.*")) if overrides_dir.exists() else []
            if override_matches:
                to_upload.append((e, override_matches[0]))
            else:
                print(f"  [ATTENZIONE] Prodotto {pid} segnato 'custom' ma nessuna foto trovata in {overrides_dir}")

    print(f"\n=== Categoria '{category}': {len(to_upload)} da caricare ===")
    if not to_upload:
        return

    for entry, image_path in to_upload:
        pid = entry["product_id"]
        name = entry["name"]

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

        upload_log_path.write_text(json.dumps(upload_log, indent=2, ensure_ascii=False), encoding="utf-8")


def main():
    dry_run = "--dry-run" in sys.argv
    categories = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not categories:
        print("Uso: python app/upload.py <categoria> [<categoria2> ...] [--dry-run]")
        sys.exit(1)

    for category in categories:
        upload_category(category, dry_run)


if __name__ == "__main__":
    main()
