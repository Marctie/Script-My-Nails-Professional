"""
Modulo OPZIONALE: rigenerazione immagine tramite OpenAI (gpt-image-1), image EDIT con maschera.
Da usare SOLO su richiesta esplicita della cliente per singolo prodotto, a suo rischio:
un modello generativo puo' alterare leggermente testo/font/colore anche con istruzioni rigorose.
La pipeline di default (process.py) NON usa questo modulo.

Uso:
  python app/ai_regen.py <product_id>

Richiede OPENAI_API_KEY nel file .env (aggiungere manualmente, non e' presente di default).
"""
import base64
import json
import sys
from pathlib import Path

from dotenv import load_dotenv
import os

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

PROMPT = (
    "Ricrea fedelmente l'immagine del prodotto che ti allego, rispettando queste regole in modo rigoroso: "
    "Struttura del prodotto: mantieni identiche la forma, le proporzioni, i dettagli e la prospettiva del prodotto. "
    "Non modificare, aggiungere o rimuovere alcun elemento. "
    "Testo: riproduci esattamente tutti i testi, le scritte, i loghi e le etichette presenti sul prodotto, "
    "senza alterarne il contenuto, il font, la dimensione o la posizione. "
    "Colori: mantieni fedelmente tutti i colori originali del prodotto, senza variazioni di tonalita', saturazione o luminosita'. "
    "Sfondo: sostituisci lo sfondo originale con uno sfondo bianco puro (#FFFFFF), uniforme e senza ombre, gradienti o texture. "
    "Il risultato deve essere una foto professionale del prodotto su sfondo bianco, come quelle usate per gli e-commerce, "
    "in cui l'unica differenza rispetto all'originale e' lo sfondo."
)


def regen_product_image(product_id: int):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("ERRORE: manca OPENAI_API_KEY nel file .env. Aggiungila prima di usare questo modulo.")
        sys.exit(1)

    from openai import OpenAI
    client = OpenAI(api_key=api_key)

    manifest = json.loads((ROOT / "backup" / "manifest" / "manifest.json").read_text(encoding="utf-8"))
    entry = next((e for e in manifest if e["product_id"] == product_id), None)
    if entry is None:
        print(f"Prodotto {product_id} non trovato nel manifest di backup.")
        sys.exit(1)

    src_path = ROOT / entry["backup_local_path"]
    out_dir = ROOT / "processed" / "ai_regen"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Rigenero via IA: {entry['name']} (rischio di alterazioni, verificare manualmente il risultato)")
    with open(src_path, "rb") as f:
        result = client.images.edit(
            model="gpt-image-1",
            image=f,
            prompt=PROMPT,
        )

    image_b64 = result.data[0].b64_json
    out_path = out_dir / f"{product_id}_{entry.get('sku') or 'noSKU'}_ai.png"
    out_path.write_bytes(base64.b64decode(image_b64))
    print(f"Salvato: {out_path}")
    print("IMPORTANTE: verifica manualmente testo/loghi/colore prima di caricarlo sul sito.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Uso: python app/ai_regen.py <product_id>")
        sys.exit(1)
    regen_product_image(int(sys.argv[1]))
