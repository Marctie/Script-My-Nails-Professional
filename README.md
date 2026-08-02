# Script My Nails Professional - Automazione immagini prodotto

Flusso principale (nessuna elaborazione AI, gira interamente su Termux):
mostra alla cliente la foto attuale di ogni prodotto su un sito di revisione,
lei puo' caricare una sua foto sostitutiva che viene pubblicata SUBITO su
WooCommerce, oppure segnare che per ora va bene cosi'. Nessuna elaborazione
tocca il sito finche' lei non carica esplicitamente una foto.

Esiste anche uno script separato, `app/process.py` (isola boccetta +
anteprima unghia da una foto e le ricompone in un layout uniforme), che usa
librerie pesanti (rembg/numpy/scipy) e NON fa piu' parte del flusso di
revisione di default: il sito statico ora mostra solo la foto attuale, non
piu' un confronto con una proposta elaborata automaticamente. Va lanciato a
mano sul PC solo se un giorno vuoi generare tu una proposta alternativa per
un prodotto, al di fuori del flusso automatico.

## Setup (una tantum)

```
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

Le credenziali WooCommerce sono gia' in `.env` (non versionato su git).

## Flusso di lavoro consigliato (automatico, gira su Termux)

Tutti gli script accettano una o piu' categorie come argomento (slug WooCommerce,
es. `color-gel`, `acrygel`, `semi-permanente`). Senza argomenti usano `WC_CATEGORY_SLUG`
dal `.env`.

1. **Backup** (scarica tutte le foto originali + dati prodotto, non modifica nulla):
   ```
   python app/backup.py color-gel acrygel semi-permanente
   ```
   Risultato per ogni categoria: `backup/<categoria>/images/` e `backup/<categoria>/manifest/manifest.json`.
   Va rifatto ogni volta che aggiungi prodotti nuovi o cambi la foto attuale a mano.

2. **Genera il sito di revisione per la cliente**:
   ```
   python app/build_static_site.py
   ```
   Genera/aggiorna la cartella `docs/` (pubblicata automaticamente da GitHub Pages
   se abilitato nelle impostazioni del repo, branch `master`/`main`, cartella `/docs`).
   Il sito mostra solo la foto attuale di ogni prodotto (nessuna proposta elaborata):
   la cliente puo' scaricarla, caricarne una sua (pubblicata SUBITO da `live_server.py`,
   vedi sezione sotto) o segnare che va bene cosi'. Guida per lei: `docs/guida.html`
   (o `GUIDA_CLIENTE.md`).

3. **Avvia `live_server.py`** (vedi sezione "Pubblicazione automatica in tempo reale"
   sotto) — riceve le scelte della cliente e pubblica in automatico, senza bisogno
   che tu intervenga.

## Modulo opzionale: elaborazione con cutout automatico (rembg)

Se in futuro vuoi generare TU una foto alternativa elaborata (isola boccetta +
anteprima unghia da una foto e le ricompone in un layout uniforme), esiste
`app/process.py` — usa pero' librerie pesanti (rembg/numpy/scipy) e va lanciato
a mano sul PC, non fa parte del flusso automatico su Termux:
```
python app/process.py color-gel acrygel semi-permanente
```
Risultato per ogni categoria: `processed/<categoria>/<id>.png`. Con questo puoi
anche usare `app/dashboard.py` (revisione locale a http://127.0.0.1:5000) e
`app/upload.py` per caricare a mano le immagini elaborate approvate — flusso
manuale, indipendente da quello automatico sopra.

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
