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

## Pubblicazione automatica in tempo reale (live_server.py + Termux-Launcher)

Invece del flusso manuale sopra, `app/live_server.py` e' un server che sta sempre acceso
su Termux (tablet Android sempre connesso, gestito da Termux-Launcher come gli altri bot
Telegram gia' in uso) e riceve in tempo reale le scelte della cliente dal sito pubblico,
pubblicando subito su WooCommerce. La chiave segreta resta sempre sul tablet, mai nella
pagina pubblica.

Questo progetto vive come cartella sorella di `Termux-Launcher/` (dentro `Dev/Bot
Telegram/`), esattamente come gli altri bot, e usa la stessa infrastruttura:
- `requirements.txt` (root del progetto) e' gia' la versione leggera per Termux (solo
  Flask/flask-cors/requests/WooCommerce/python-dotenv). Le librerie pesanti di elaborazione
  immagini (rembg, onnxruntime, scipy, pillow...) sono in `requirements-processing.txt`,
  usato SOLO sul PC per backup.py/process.py/build_static_site.py.
- Riga gia' aggiunta in `Termux-Launcher/bots.conf`:
  `nails_live|My Nails - Pubblicazione Foto|Script My Nails Professional|app/live_server.py|nails_live.log`

Setup:
1. Su Termux, imposta `REVIEW_API_TOKEN` nel `.env` del progetto (token gia' generato).
2. Lancia (una volta, o dopo aver modificato requirements.txt):
   `bash ~/bots/Termux-Launcher/install.sh` — crea il venv e installa le dipendenze leggere.
3. Avvia/ferma/riavvia con la dashboard su `http://127.0.0.1:8765` (voce "My Nails -
   Pubblicazione Foto"), oppure da terminale:
   `bash ~/bots/Termux-Launcher/bot_ctl.sh nails_live start|stop|restart`
4. Esponi la porta 5001 con un tunnel pubblico (es. Cloudflare Tunnel, disponibile anche
   su Termux) per ottenere un URL raggiungibile da internet.
5. Apri `docs/config.js` e imposta:
   ```js
   const LIVE_SERVER_URL = "https://tuo-tunnel.esempio.com";
   const LIVE_SERVER_TOKEN = "lo-stesso-valore-di-REVIEW_API_TOKEN";
   ```
   Questo file NON viene sovrascritto da `build_static_site.py` una volta creato, quindi
   resta configurato anche rigenerando il sito.
6. Committa e pusha: la pagina pubblica ora invia automaticamente ogni scelta al server,
   che pubblica subito su WooCommerce.

Monitoraggio: la dashboard di Termux-Launcher (http://127.0.0.1:8765) mostra gia' stato
attivo/fermo, uptime, CPU/RAM e ultime righe di log per "nails_live" come per ogni altro
bot. In aggiunta, live_server.py offre:
- `GET /api/health` -> stato + uptime
- `GET /api/stats` -> contatori (richieste, pubblicazioni, rifiuti, errori) ed eventi recenti
- Log dettagliato in `logs/live_server.log` (oltre al log principale che Termux-Launcher
  tiene in `logs/nails_live.log`)

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
