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

## Pubblicazione automatica in tempo reale (live_server.py)

Invece del flusso manuale sopra, `app/live_server.py` e' un server che sta sempre acceso
(pensato per girare su Termux, tablet Android sempre connesso, come i bot Telegram gia' in
uso) e riceve in tempo reale le scelte della cliente dal sito pubblico, pubblicando subito
su WooCommerce. La chiave segreta resta sempre sul dispositivo che fa girare il server,
mai nella pagina pubblica.

Setup:
1. Sul dispositivo che fara' girare il server (Termux o PC), imposta `REVIEW_API_TOKEN`
   nel `.env` (un token a caso, gia' generato in questo progetto).
2. Avvia: `python app/live_server.py` (oppure `bash scripts/start.sh` su Termux).
   Controllo: `bash scripts/status.sh`, `bash scripts/stop.sh`, `bash scripts/restart.sh`.
3. Esponi la porta 5001 con un tunnel pubblico (es. Cloudflare Tunnel o servizio simile
   disponibile su Termux) per ottenere un URL raggiungibile da internet.
4. Apri `docs/config.js` e imposta:
   ```js
   const LIVE_SERVER_URL = "https://tuo-tunnel.esempio.com";
   const LIVE_SERVER_TOKEN = "lo-stesso-valore-di-REVIEW_API_TOKEN";
   ```
   Questo file NON viene sovrascritto da `build_static_site.py` una volta creato, quindi
   resta configurato anche rigenerando il sito.
5. Committa e pusha: la pagina pubblica ora invia automaticamente ogni scelta al server,
   che pubblica subito su WooCommerce.

Monitoraggio (per integrare con una dashboard esterna):
- `GET /api/health` -> stato + uptime
- `GET /api/stats` -> contatori (richieste, pubblicazioni, rifiuti, errori) ed eventi recenti
- Log leggibile in `logs/live_server.log`
- PID in `logs/live_server.pid` (usato dagli script start/stop/restart in `scripts/`)

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
