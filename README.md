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

## Flusso di lavoro (per categoria)

Tutti gli script accettano una o piu' categorie come argomento (slug WooCommerce,
es. `color-gel`, `acrygel`, `semi-permanente`). Senza argomenti usano `WC_CATEGORY_SLUG`
dal `.env`.

1. **Backup** (scarica tutte le foto originali + dati prodotto, non modifica nulla):
   ```
   python app/backup.py color-gel acrygel semi-permanente
   ```
   Risultato per ogni categoria: `backup/<categoria>/images/` e `backup/<categoria>/manifest/manifest.json`.

2. **Elaborazione** (cutout + ricomposizione uniforme, salva solo in locale):
   ```
   python app/process.py color-gel acrygel semi-permanente
   ```
   Risultato per ogni categoria: `processed/<categoria>/<id>.png` (immagine finale) e
   `processed/<categoria>/preview/<id>.png` (originale affiancato al risultato).

3a. **Revisione locale (per te)**: dashboard web con tutte le categorie a tab:
   ```
   python app/dashboard.py
   ```
   Apri http://127.0.0.1:5000 nel browser.

3b. **Revisione dalla cliente (sito statico su GitHub Pages)**:
   ```
   python app/build_static_site.py
   ```
   Genera/aggiorna la cartella `docs/` (pubblicata automaticamente da GitHub Pages
   se abilitato nelle impostazioni del repo, branch `master`/`main`, cartella `/docs`).
   La cliente apre il link, approva/rifiuta ogni prodotto (per categoria). Per ogni
   prodotto puo' anche scaricare la foto attuale o caricare una SUA foto alternativa
   (se non le piace nessuna delle due proposte). Alla fine scarica un file
   `revisione_YYYY-MM-DD.json` e te lo invia. Guida per lei: `docs/guida.html`
   (o `GUIDA_CLIENTE.md`).

   Quando ricevi il file, importalo per aggiornare lo stato di revisione:
   ```
   python app/import_reviews.py percorso/al/file/revisione_2026-08-01.json
   ```
   Questo aggiorna `processed/<categoria>/review_state.json` e salva eventuali foto
   personalizzate della cliente in `processed/<categoria>/client_overrides/`.
   Poi rielabora solo i prodotti rifiutati e rigenera il sito statico per la
   nuova revisione.

4. **Upload** (carica su WooCommerce SOLO le immagini approvate, per categoria):
   ```
   python app/upload.py color-gel --dry-run   # verifica cosa farebbe, senza caricare
   python app/upload.py color-gel acrygel semi-permanente   # carica davvero
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
