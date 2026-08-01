"""
Backup dei prodotti WooCommerce (per categoria) e delle relative immagini.
Salva, per ogni categoria elaborata:
  - backup/<categoria>/images/<product_id>_<sku>.png  -> immagine originale
  - backup/<categoria>/manifest/manifest.json          -> id, nome, sku, url immagine originale, path locale
Non modifica nulla su WordPress: e' solo lettura + salvataggio locale.

Uso:
  python app/backup.py                          # usa WC_CATEGORY_SLUG da .env
  python app/backup.py color-gel acrygel semi-permanente   # una o piu' categorie esplicite
"""
import os
import json
import re
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv
from woocommerce import API

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

SITE_URL = os.environ["WC_SITE_URL"]
CONSUMER_KEY = os.environ["WC_CONSUMER_KEY"]
CONSUMER_SECRET = os.environ["WC_CONSUMER_SECRET"]

wcapi = API(
    url=SITE_URL,
    consumer_key=CONSUMER_KEY,
    consumer_secret=CONSUMER_SECRET,
    version="wc/v3",
    timeout=30,
)


def safe_filename(text: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_-]+", "_", text.strip())
    return text.strip("_") or "unnamed"


def find_category_id(slug: str) -> int:
    resp = wcapi.get("products/categories", params={"slug": slug})
    resp.raise_for_status()
    data = resp.json()
    if not data:
        print(f"ERRORE: nessuna categoria trovata con slug '{slug}'.")
        print("Suggerimento: controlla lo slug esatto su WooCommerce > Prodotti > Categorie.")
        sys.exit(1)
    return data[0]["id"]


def fetch_products(category_id: int):
    products = []
    page = 1
    while True:
        resp = wcapi.get(
            "products",
            params={"category": category_id, "per_page": 100, "page": page, "status": "publish"},
        )
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        products.extend(batch)
        page += 1
    return products


def download_image(url: str, dest: Path):
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    dest.write_bytes(r.content)


def backup_category(category_slug: str):
    images_dir = ROOT / "backup" / category_slug / "images"
    manifest_dir = ROOT / "backup" / category_slug / "manifest"
    images_dir.mkdir(parents=True, exist_ok=True)
    manifest_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n=== Categoria '{category_slug}' ===")
    print(f"Cerco categoria '{category_slug}'...")
    category_id = find_category_id(category_slug)
    print(f"Categoria trovata (id={category_id}). Scarico elenco prodotti...")

    products = fetch_products(category_id)
    print(f"Trovati {len(products)} prodotti.")

    manifest = []
    for p in products:
        pid = p["id"]
        name = p["name"]
        sku = p.get("sku") or ""
        images = p.get("images") or []

        if not images:
            print(f"  [ATTENZIONE] Prodotto {pid} ({name}) non ha immagini, salto.")
            continue

        main_image = images[0]
        image_url = main_image["src"]
        image_id_wp = main_image["id"]
        ext = Path(image_url.split("?")[0]).suffix or ".jpg"

        filename = f"{pid}_{safe_filename(sku or name)}{ext}"
        dest_path = images_dir / filename

        try:
            download_image(image_url, dest_path)
            status = "ok"
        except Exception as e:
            status = f"errore: {e}"
            print(f"  [ERRORE] Prodotto {pid} ({name}): {e}")

        manifest.append({
            "product_id": pid,
            "category": category_slug,
            "name": name,
            "sku": sku,
            "permalink": p.get("permalink"),
            "original_image_url": image_url,
            "original_image_wp_id": image_id_wp,
            "backup_local_path": str(dest_path.relative_to(ROOT)),
            "status": status,
        })
        print(f"  [{status}] {pid} - {name}")

    manifest_path = manifest_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Backup completato per '{category_slug}': {len(manifest)} prodotti salvati.")
    print(f"Manifest: {manifest_path}")
    print(f"Immagini: {images_dir}")


def main():
    categories = sys.argv[1:] or [os.environ["WC_CATEGORY_SLUG"]]
    for slug in categories:
        backup_category(slug)


if __name__ == "__main__":
    main()
