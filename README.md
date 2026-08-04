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

Invece del flusso manuale sopra, `app/live_server.py` e' un processo che sta sempre acceso
su Termux (tablet Android sempre connesso, gestito da Termux-Launcher come gli altri bot
Telegram gia' in uso), **completamente autonomo e indipendente da ogni altro bot**
(nessuna dipendenza da "Centro di Comando" o da altri processi: se va offline, e' il
watchdog di Termux-Launcher, gia' in uso per tutti gli altri bot, a farlo ripartire da
solo). NON e' un server esposto su internet e NON serve alcun tunnel:

- **In ingresso**: il sito pubblico scrive ogni scelta della cliente (categoria, prodotto,
  stato, eventuale foto) come file JSON nella cartella `queue/` del **repository GitHub**,
  usando l'API Contents (nessun bot Telegram coinvolto: un bot non puo' mai ricevere via
  `getUpdates` i messaggi scritti da un altro bot, limite strutturale di Telegram scoperto
  e verificato in una sessione precedente — per questo l'ingresso ora passa da GitHub, non
  piu' da Telegram). `live_server.py` fa polling su quella cartella (ogni ~10 secondi),
  elabora ogni richiesta e la cancella dalla coda.
- **In uscita**: tutte le notifiche (ricezione, backup, upload, conferma o errore) vengono
  mandate SOLO tramite il bot Telegram **"My Nails Live"** (gia' esistente, dedicato a
  questo bot) direttamente alla tua chat — un invio (`sendMessage`) funziona sempre, non
  soffre del limite sopra perche' non deve mai "ricevere" nulla.

La chiave WooCommerce resta sempre sul tablet, mai nella pagina pubblica.

Questo progetto vive come cartella sorella di `Termux-Launcher/` (dentro `Dev/Bot
Telegram/`), esattamente come gli altri bot, e usa la stessa infrastruttura:
- `requirements.txt` (root del progetto) e' gia' la versione leggera per Termux (solo
  Flask/requests/WooCommerce/python-dotenv/Pillow — Flask serve solo per health/stats
  locali, non e' piu' esposto pubblicamente). Le librerie pesanti di elaborazione
  immagini (rembg, onnxruntime, scipy...) sono in `requirements-processing.txt`,
  usato SOLO sul PC per backup.py/process.py.
- Riga gia' aggiunta in `Termux-Launcher/bots.conf`:
  `nails_live|My Nails - Pubblicazione Foto|Script My Nails Professional|app/live_server.py|nails_live.log`

Setup (una tantum):
1. Crea un **GitHub fine-grained Personal Access Token**: github.com -> Settings ->
   Developer settings -> Fine-grained tokens -> Generate new -> **Only select
   repositories** -> scegli solo questo repo -> permessi **Repository permissions ->
   Contents: Read and write** (nessun altro permesso). Questo token va incollato in
   `docs/config.js` (vedi sotto): e' pubblico nel sorgente della pagina, quindi il rischio
   se qualcuno lo trovasse e' limitato a scrivere/cancellare file in questo repository,
   niente di piu' — revocabile in un click dalle impostazioni GitHub in qualsiasi momento.
2. Crea un secondo token fine-grained identico (stessi permessi, stesso repo) da tenere
   **privato**, solo sul tablet: va nel `.env` di Termux come `GITHUB_TOKEN`, usato da
   `live_server.py` per leggere/cancellare la coda. Separato dal primo cosi' puoi revocare
   quello pubblico senza rompere quello che gira su Termux.
3. Su Termux, nel `.env` del progetto imposta:
   ```
   GITHUB_TOKEN=...           (il secondo token, privato, del punto 2)
   GITHUB_REPO=Marctie/Script-My-Nails-Professional
   GITHUB_BRANCH=master
   TELEGRAM_LIVE_BOT_TOKEN=... (token del bot "My Nails Live")
   TELEGRAM_LIVE_CHAT_ID=...   (il tuo chat id Telegram)
   ```
4. Lancia (una volta, o dopo aver modificato requirements.txt):
   `bash ~/bots/Termux-Launcher/install.sh` — crea il venv e installa le dipendenze leggere.
5. Avvia/ferma/riavvia con la dashboard su `http://127.0.0.1:8765` (voce "My Nails -
   Pubblicazione Foto"), oppure da terminale:
   `bash ~/bots/Termux-Launcher/bot_ctl.sh nails_live start|stop|restart`
6. Apri `docs/config.js` e imposta:
   ```js
   const GITHUB_TOKEN = "il-primo-token-del-punto-1-quello-pubblico";
   const GITHUB_REPO = "Marctie/Script-My-Nails-Professional";
   const GITHUB_BRANCH = "master";
   ```
   Questo file NON viene sovrascritto da `build_static_site.py` una volta creato, quindi
   resta configurato anche rigenerando il sito.
7. Committa e pusha: la pagina pubblica ora scrive automaticamente ogni scelta nella coda
   GitHub, `live_server.py` su Termux la vede entro ~10 secondi e pubblica su WooCommerce.

Monitoraggio: la dashboard di Termux-Launcher (http://127.0.0.1:8765) mostra gia' stato
attivo/fermo, uptime, CPU/RAM e ultime righe di log per "nails_live" come per ogni altro
bot (con riavvio automatico via watchdog se il processo dovesse fermarsi). In aggiunta,
live_server.py offre (solo localmente su Termux, non su internet):
- `GET http://127.0.0.1:5001/api/health` -> stato + uptime
- `GET http://127.0.0.1:5001/api/stats` -> contatori (richieste, pubblicazioni, rifiuti, errori) ed eventi recenti
- Log dettagliato in `logs/live_server.log` (oltre al log principale che Termux-Launcher
  tiene in `logs/nails_live.log`)
- Notifiche dettagliate su Telegram (bot "My Nails Live") per ogni azione sui singoli
  prodotti: ricezione foto, backup della foto attuale prima della sostituzione, upload
  riuscito/fallito, conferma "nessuna modifica" della cliente, o errore con motivo.

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
