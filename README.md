# Script My Nails Professional - Automazione immagini prodotto

Automatizza l'aggiornamento delle foto prodotto (categoria Color Gel) su WooCommerce:
prende la foto attuale (boccetta + anteprima unghia), isola i due soggetti, li ricompone
in un layout uniforme su sfondo bianco, e — solo dopo tua approvazione manuale in una
dashboard di revisione — aggiorna il prodotto sul sito.

Nessuna elaborazione tocca il sito finche' non approvi esplicitamente ogni immagine.

## Setup (una tantum)

```
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

Le credenziali WooCommerce sono gia' in `.env` (non versionato su git).

## Flusso di lavoro

1. **Backup** (scarica tutte le foto originali + dati prodotto, non modifica nulla):
   ```
   python app/backup.py
   ```
   Risultato: `backup/images/` (foto originali) e `backup/manifest/manifest.json`.

2. **Elaborazione** (cutout + ricomposizione uniforme, salva solo in locale):
   ```
   python app/process.py
   ```
   Risultato: `processed/<id>.png` (immagine finale) e `processed/preview/<id>.png`
   (originale affiancato al risultato, per revisione rapida).

3. **Revisione** (dashboard web locale per approvare/rifiutare ogni immagine):
   ```
   python app/dashboard.py
   ```
   Apri http://127.0.0.1:5000 nel browser. Approva o rifiuta ogni prodotto.

4. **Upload** (carica su WooCommerce SOLO le immagini approvate):
   ```
   python app/upload.py --dry-run   # verifica cosa farebbe, senza caricare
   python app/upload.py             # carica davvero
   ```

## Recupero in caso di errore

Tutte le foto originali restano in `backup/images/`, con la mappatura prodotto <-> foto
in `backup/manifest/manifest.json` (id prodotto, nome, SKU, url originale, id media WordPress
originale). Per ripristinare un'immagine originale su un prodotto, basta ricaricare quel file
su WooCommerce con lo stesso procedimento di `upload.py` usando il path del backup invece del
processed.

## Modulo opzionale: rigenerazione via IA (ChatGPT / gpt-image-1)

Da usare SOLO su richiesta esplicita della cliente per un singolo prodotto, perche' un
modello generativo puo' alterare leggermente testo/font/colore anche con istruzioni rigorose
(a differenza della pipeline di default, che riusa i pixel originali).

Richiede una chiave OpenAI: aggiungi nel `.env` la riga `OPENAI_API_KEY=...`

```
python app/ai_regen.py <product_id>
```

Il risultato va sempre verificato manualmente prima di caricarlo (non passa dalla dashboard
automaticamente).
