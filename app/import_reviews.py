"""
Importa il file "revisione_YYYY-MM-DD.json" scaricato dalla cliente dal sito
statico (docs/) e aggiorna processed/<categoria>/review_state.json per ogni
categoria presente nel file. Se la cliente ha caricato una SUA foto per un
prodotto (stato "custom"), la foto (in base64 nel file) viene salvata in
processed/<categoria>/client_overrides/<product_id>.<estensione> cosi'
upload.py la usa al posto di quella elaborata automaticamente.

Dopo l'import, i prodotti rifiutati possono essere rielaborati (process.py)
e quelli approvati/custom caricati (upload.py).

Uso:
  python app/import_reviews.py percorso/al/file/revisione_2026-08-01.json
"""
import base64
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROCESSED_ROOT = ROOT / "processed"


def save_custom_image(category: str, product_id: str, data_url: str) -> str:
    header, b64data = data_url.split(",", 1)
    ext = ".jpg"
    if "image/png" in header:
        ext = ".png"
    elif "image/webp" in header:
        ext = ".webp"

    overrides_dir = PROCESSED_ROOT / category / "client_overrides"
    overrides_dir.mkdir(parents=True, exist_ok=True)
    dest = overrides_dir / f"{product_id}{ext}"
    dest.write_bytes(base64.b64decode(b64data))
    return str(dest.relative_to(ROOT))


def main():
    if len(sys.argv) != 2:
        print("Uso: python app/import_reviews.py <percorso file revisione.json>")
        sys.exit(1)

    import_path = Path(sys.argv[1])
    incoming = json.loads(import_path.read_text(encoding="utf-8"))

    for category, decisions in incoming.items():
        category_dir = PROCESSED_ROOT / category
        if not category_dir.exists():
            print(f"[ATTENZIONE] Categoria '{category}' non trovata in processed/, salto.")
            continue

        review_state_path = category_dir / "review_state.json"
        current = json.loads(review_state_path.read_text(encoding="utf-8")) if review_state_path.exists() else {}

        approved = rejected = custom = 0
        rejected_ids = []

        for product_id, value in decisions.items():
            # Compatibilita': valore puo' essere una stringa (vecchio formato) o un oggetto
            if isinstance(value, str):
                status = value
                custom_image_data_url = None
            else:
                status = value.get("status", "pending")
                custom_image_data_url = value.get("customImage")

            current[product_id] = status

            if status == "approved":
                approved += 1
            elif status == "rejected":
                rejected += 1
                rejected_ids.append(product_id)
            elif status == "custom" and custom_image_data_url:
                custom += 1
                saved_path = save_custom_image(category, product_id, custom_image_data_url)
                print(f"    [{category}] foto personalizzata salvata per prodotto {product_id}: {saved_path}")

        review_state_path.write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")

        print(f"[{category}] importate {len(decisions)} decisioni "
              f"({approved} approvate, {rejected} rifiutate, {custom} con foto propria)")
        if rejected_ids:
            print(f"    Da rielaborare: {rejected_ids}")

    print("\nFatto. Ora puoi:")
    print("  - rielaborare i rifiutati modificando/ritentando process.py per quei prodotti")
    print("  - lanciare 'python app/upload.py <categoria>' per pubblicare approvati e foto proprie")


if __name__ == "__main__":
    main()
