"""
Genera il sito statico di revisione per GitHub Pages in docs/.
Legge backup/<categoria>/manifest/manifest.json (creato da backup.py) e copia
la foto attuale di ogni prodotto, ridimensionata, in docs/assets/<categoria>/.

Nessuna elaborazione AI: il sito mostra solo la foto attuale di ogni
prodotto. La cliente puo' caricare una sua foto sostitutiva, che viene
pubblicata subito da live_server.py, oppure segnare che per ora va bene
cosi'. La scelta viene salvata SOLO nel suo browser (localStorage), perche'
GitHub Pages e' hosting statico e non ha un server dietro. A fine revisione
clicca "Scarica risultati" e invia il file JSON scaricato a Marco.

Uso:
  python app/build_static_site.py                     # tutte le categorie in backup/
  python app/build_static_site.py color-gel acrygel    # solo alcune categorie
"""
import json
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
BACKUP_ROOT = ROOT / "backup"
DOCS_ROOT = ROOT / "docs"
ASSETS_ROOT = DOCS_ROOT / "assets"
DATA_ROOT = DOCS_ROOT / "data"

MAX_WEB_WIDTH = 800


def resize_for_web(src_path: Path, dest_path: Path, max_width: int = MAX_WEB_WIDTH):
    img = Image.open(src_path).convert("RGB")
    if img.width > max_width:
        ratio = max_width / img.width
        img = img.resize((max_width, int(img.height * ratio)), Image.LANCZOS)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest_path, quality=85, optimize=True)


def build_category(category_slug: str):
    manifest_path = BACKUP_ROOT / category_slug / "manifest" / "manifest.json"
    if not manifest_path.exists():
        print(f"[{category_slug}] Nessun backup trovato, salto (lancia prima app/backup.py).")
        return None

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    items = []

    orig_dir = ASSETS_ROOT / category_slug / "original"

    for entry in manifest:
        if entry.get("status") != "ok":
            continue
        pid = entry["product_id"]

        src_original = ROOT / entry["backup_local_path"]
        original_web_name = f"{pid}.jpg"

        resize_for_web(src_original, orig_dir / original_web_name)

        items.append({
            "product_id": pid,
            "name": entry["name"],
            "original_image": f"assets/{category_slug}/original/{original_web_name}",
        })

    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    data_path = DATA_ROOT / f"{category_slug}.json"
    data_path.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[{category_slug}] {len(items)} prodotti pronti per il sito statico.")
    return category_slug


def write_index_html():
    (DOCS_ROOT / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (DOCS_ROOT / "app.js").write_text(APP_JS, encoding="utf-8")
    (DOCS_ROOT / "styles.css").write_text(STYLES_CSS, encoding="utf-8")
    (DOCS_ROOT / "guida.html").write_text(GUIDA_HTML, encoding="utf-8")

    config_path = DOCS_ROOT / "config.js"
    if not config_path.exists():
        config_path.write_text(DEFAULT_CONFIG_JS, encoding="utf-8")
        print(f"Creato {config_path} (vuoto, da riempire con URL/token del server live).")
    else:
        print(f"{config_path} gia' presente, non sovrascritto (URL/token preservati).")


INDEX_HTML = """<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Revisione foto prodotti - My Nails Professional</title>
<link rel="stylesheet" href="styles.css">
</head>
<body>
  <header>
    <h1>Revisione foto prodotti</h1>
    <a class="guide-link" href="guida.html">Come si usa? (guida semplice)</a>
  </header>

  <div class="toolbar">
    <div class="tabs" id="tabs"></div>
    <div class="toolbar-actions">
      <button id="exportBtn">Scarica risultati</button>
      <label class="import-label">
        Importa file precedente
        <input type="file" id="importFile" accept="application/json">
      </label>
    </div>
  </div>

  <div class="stats" id="stats"></div>
  <div class="grid" id="grid"></div>

  <script src="config.js"></script>
  <script src="app.js"></script>
</body>
</html>
"""

# config.js NON viene sovrascritto se esiste gia' (build_static_site() lo scrive solo
# la prima volta), cosi' l'URL/token del server live impostati a mano sopravvivono
# alle rigenerazioni successive del sito.
DEFAULT_CONFIG_JS = """// Configurazione del bot Telegram dedicato (gira su Termux, in polling,
// nessun tunnel/porta esposta) che riceve le scelte della cliente e pubblica
// su WooCommerce. Lascia TELEGRAM_BOT_TOKEN vuoto per usare SOLO il
// salvataggio locale + export manuale (nessuna pubblicazione automatica).
const TELEGRAM_BOT_TOKEN = "";
const TELEGRAM_CHAT_ID = "";
const REVIEW_TOKEN = "";
"""

STYLES_CSS = """
* { box-sizing: border-box; }
body { font-family: Arial, Helvetica, sans-serif; background: #f5f5f5; margin: 0; padding: 20px; color: #222; }
header { display: flex; align-items: baseline; gap: 16px; margin-bottom: 16px; flex-wrap: wrap; }
h1 { font-size: 20px; margin: 0; }
.guide-link { font-size: 14px; color: #1565c0; text-decoration: none; }
.guide-link:hover { text-decoration: underline; }

.toolbar { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; margin-bottom: 12px; }
.tabs { display: flex; gap: 8px; flex-wrap: wrap; }
.tabs button { padding: 8px 16px; border: 1px solid #ccc; background: white; border-radius: 6px; cursor: pointer; font-size: 14px; }
.tabs button.active { background: #1565c0; color: white; border-color: #1565c0; }

.toolbar-actions { display: flex; gap: 10px; align-items: center; }
#exportBtn { padding: 10px 18px; border: none; border-radius: 6px; background: #2e7d32; color: white; cursor: pointer; font-size: 14px; }
.import-label { font-size: 13px; background: white; border: 1px solid #ccc; padding: 8px 12px; border-radius: 6px; cursor: pointer; }
.import-label input { display: none; }

.stats { margin-bottom: 20px; font-size: 14px; color: #444; }

.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(380px, 1fr)); gap: 16px; }
.card { background: white; border-radius: 8px; padding: 12px; box-shadow: 0 1px 3px rgba(0,0,0,0.15); }
.card .images { display: flex; gap: 6px; }
.card .images figure { flex: 1; margin: 0; text-align: center; }
.card .images img { width: 100%; border: 1px solid #ddd; border-radius: 4px; background: white; }
.card .images figcaption { font-size: 11px; color: #777; margin-top: 4px; }
.name { font-weight: bold; margin: 10px 0 4px; font-size: 14px; }
.actions { margin-top: 8px; display: flex; gap: 8px; }
button.act { flex: 1; padding: 10px; border: none; border-radius: 4px; cursor: pointer; font-size: 14px; }
.no-change { background: #2e7d32; color: white; }
.no-change.active { outline: 3px solid #a5d6a7; }
.confirm-publish { background: #e65100; color: white; font-weight: bold; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; margin-left: 6px; }
.badge.rejected { background: #c8e6c9; color: #256029; }
.badge.pending { background: #eeeeee; color: #555; }
.badge.custom { background: #bbdefb; color: #0d47a1; }

.secondary-actions { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-top: 10px; font-size: 12px; }
.download-link { color: #1565c0; text-decoration: none; border: 1px solid #1565c0; padding: 6px 10px; border-radius: 4px; }
.download-link:hover { background: #e3f2fd; }
.upload-label { color: #0d47a1; border: 1px solid #0d47a1; padding: 6px 10px; border-radius: 4px; cursor: pointer; background: #e3f2fd; }
.upload-label:hover { background: #bbdefb; }
.publish-state { font-size: 12px; color: #666; margin: -4px 0 8px; font-style: italic; }
"""

APP_JS = """
const CATEGORIES_HINT = ["color-gel", "acrygel", "semi-permanente"];
let currentCategory = null;
let currentItems = [];

function reviewKey(category, productId) {
  return `review_${category}_${productId}`;
}

// customImage (la foto in base64) non viene MAI scritta su localStorage:
// serve solo per l'anteprima e per l'invio al Worker, quindi la teniamo in
// memoria (per la sessione corrente) per non riempire la quota del browser
// (~5-10MB) dopo poche foto.
const customImageCache = {};

function getReviewData(category, productId) {
  const key = reviewKey(category, productId);
  const raw = localStorage.getItem(key);
  let data;
  if (!raw) data = { status: "pending" };
  else {
    try {
      data = JSON.parse(raw);
    } catch (e) {
      data = { status: raw }; // compatibilita' con vecchio formato (solo stringa)
    }
  }
  if (customImageCache[key]) data.customImage = customImageCache[key];
  return data;
}

function saveReviewData(category, productId, data) {
  const key = reviewKey(category, productId);
  const { customImage, ...toPersist } = data;
  if (customImage) customImageCache[key] = customImage;
  try {
    localStorage.setItem(key, JSON.stringify(toPersist));
  } catch (e) {
    console.warn("localStorage pieno, salvo solo in memoria per questa sessione:", e);
  }
}

// Ridimensiona/comprime l'immagine lato client prima di salvarla e inviarla,
// cosi' il payload resta piccolo (la foto di un telefono puo' essere 3-8MB,
// troppo per l'API "Contents" di GitHub, che tronca i file oltre ~1MB quando
// vengono riletti da live_server.py).
function resizeImage(file, maxSize = 1600, quality = 0.8) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = reject;
    reader.onload = () => {
      const img = new Image();
      img.onerror = reject;
      img.onload = () => {
        let { width, height } = img;
        if (width > maxSize || height > maxSize) {
          const scale = maxSize / Math.max(width, height);
          width = Math.round(width * scale);
          height = Math.round(height * scale);
        }
        const canvas = document.createElement("canvas");
        canvas.width = width;
        canvas.height = height;
        canvas.getContext("2d").drawImage(img, 0, 0, width, height);
        resolve(canvas.toDataURL("image/jpeg", quality));
      };
      img.src = reader.result;
    };
    reader.readAsDataURL(file);
  });
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

// Manda la scelta della cliente a un Cloudflare Worker (WORKER_URL, vedi
// config.js), che scrive il file nella cartella queue/ del repo GitHub al
// posto del sito: il token GitHub reale resta nascosto lato Worker, non e'
// mai nel sorgente pubblico della pagina, quindi GitHub non lo revoca piu'
// automaticamente. Termux (live_server.py) fa poi polling sulla coda ed
// elabora/cancella la richiesta, esattamente come prima.
//
// Ritenta fino a 3 volte (con una breve pausa crescente) prima di arrendersi:
// una connessione instabile (es. rete mobile che va e viene) non deve far
// perdere una foto che altrimenti sarebbe stata inviata correttamente al
// tentativo successivo. La foto resta comunque salvata nel browser (vedi
// customImageCache/saveReviewData) finche' non risulta "inviato" con
// successo, quindi anche in caso di fallimento totale il pulsante "Conferma
// e pubblica" ricompare per riprovare manualmente piu' tardi.
const SUBMIT_MAX_ATTEMPTS = 3;
const SUBMIT_RETRY_DELAYS_MS = [2000, 5000];

async function submitToLiveServer(category, productId, status, customImage) {
  if (!WORKER_URL) return; // non configurato: resta solo salvataggio locale

  const data = getReviewData(category, productId);
  data.publishState = "invio in corso...";
  saveReviewData(category, productId, data);
  renderGrid();

  const payload = {
    category,
    product_id: productId,
    status,
    image_base64: status === "custom" && customImage ? customImage.split(",", 2)[1] : null,
    image_mime: status === "custom" && customImage ? (customImage.match(/data:(.*?);base64/) || [])[1] : null,
  };

  let lastOutcome = null;
  for (let attempt = 1; attempt <= SUBMIT_MAX_ATTEMPTS; attempt++) {
    try {
      const res = await fetch(WORKER_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (res.ok) {
        lastOutcome = "inviato (verra' pubblicato a breve)";
        break;
      }
      lastOutcome = `errore invio: HTTP ${res.status}`;
      // Un errore HTTP del Worker (es. 502/500) puo' essere transitorio
      // quanto un errore di rete: vale la pena ritentare comunque, non solo
      // sui fallimenti di fetch().
    } catch (e) {
      lastOutcome = "servizio non raggiungibile (salvato solo qui)";
    }
    if (attempt < SUBMIT_MAX_ATTEMPTS) {
      const latest = getReviewData(category, productId);
      latest.publishState = `${lastOutcome} — ritento (${attempt}/${SUBMIT_MAX_ATTEMPTS})...`;
      saveReviewData(category, productId, latest);
      renderGrid();
      await sleep(SUBMIT_RETRY_DELAYS_MS[attempt - 1]);
    }
  }

  const latest = getReviewData(category, productId);
  latest.publishState = lastOutcome;
  saveReviewData(category, productId, latest);
  renderGrid();
}

function setReview(category, productId, status) {
  const data = getReviewData(category, productId);
  data.status = status;
  saveReviewData(category, productId, data);
  renderGrid();
  submitToLiveServer(category, productId, status, data.customImage);
}

async function setCustomImage(category, productId, file) {
  const resized = await resizeImage(file);
  const data = getReviewData(category, productId);
  data.status = "custom";
  data.customImage = resized; // data URL base64, ridimensionata/compressa
  data.customImageName = file.name;
  data.publishState = null; // nuova foto: serve una nuova conferma prima di pubblicarla
  saveReviewData(category, productId, data);
  renderGrid();
}

// Invio effettivo alla pubblicazione live: parte SOLO quando la cliente
// clicca il pulsante di conferma, mai automaticamente al caricamento.
function confirmPublish(category, productId) {
  const data = getReviewData(category, productId);
  if (!data.customImage) return;
  submitToLiveServer(category, productId, "custom", data.customImage);
}

async function detectCategories() {
  const found = [];
  for (const cat of CATEGORIES_HINT) {
    try {
      const res = await fetch(`data/${cat}.json`);
      if (res.ok) found.push(cat);
    } catch (e) { /* ignore */ }
  }
  return found;
}

async function init() {
  const categories = await detectCategories();
  const tabs = document.getElementById("tabs");
  tabs.innerHTML = "";
  categories.forEach(cat => {
    const btn = document.createElement("button");
    btn.textContent = cat;
    btn.dataset.cat = cat;
    btn.onclick = () => selectCategory(cat);
    tabs.appendChild(btn);
  });
  if (categories.length) selectCategory(categories[0]);

  document.getElementById("exportBtn").onclick = exportResults;
  document.getElementById("importFile").addEventListener("change", importResults);
}

async function selectCategory(cat) {
  currentCategory = cat;
  document.querySelectorAll("#tabs button").forEach(b => {
    b.classList.toggle("active", b.dataset.cat === cat);
  });
  const res = await fetch(`data/${cat}.json`);
  currentItems = await res.json();
  renderGrid();
}

function renderGrid() {
  const grid = document.getElementById("grid");
  grid.innerHTML = "";
  let noChange = 0, pending = 0, custom = 0;

  currentItems.forEach(item => {
    const data = getReviewData(currentCategory, item.product_id);
    const status = data.status || "pending";
    if (status === "no_change") noChange++;
    else if (status === "custom") custom++;
    else pending++;

    const fileInputId = `custom-${currentCategory}-${item.product_id}`;
    const customPreview = status === "custom" && data.customImage
      ? `<figure><img src="${data.customImage}" alt="tua foto"><figcaption>La tua foto (${data.customImageName || ""})</figcaption></figure>`
      : "";

    const publishState = data.publishState
      ? `<div class="publish-state">${data.publishState}</div>`
      : "";

    // Il pulsante di conferma compare solo se c'e' una foto caricata non
    // ancora inviata (o dopo un errore di invio, per poter riprovare):
    // niente pubblicazione automatica al solo caricamento del file.
    const notYetSent = !data.publishState || data.publishState.startsWith("errore");
    const confirmButton = status === "custom" && data.customImage && notYetSent
      ? `<button class="act confirm-publish" onclick="confirmPublish('${currentCategory}', ${item.product_id})">Conferma e pubblica questa foto</button>`
      : "";

    const card = document.createElement("div");
    card.className = "card";
    card.innerHTML = `
      <div class="name">${item.name} <span class="badge ${status}">${status}</span></div>
      ${publishState}
      <div class="images">
        <figure>
          <img src="${item.original_image}" alt="attuale">
          <figcaption>Attuale</figcaption>
        </figure>
        ${customPreview}
      </div>
      <div class="secondary-actions">
        <a class="download-link" href="${item.original_image}" download="attuale_${item.product_id}.jpg">Scarica foto attuale</a>
        <label class="upload-label" for="${fileInputId}">Carica una tua foto</label>
        <input type="file" id="${fileInputId}" accept="image/*" style="display:none">
      </div>
      <div class="actions">
        <button class="act no-change ${status === 'no_change' ? 'active' : ''}"
          onclick="setReview('${currentCategory}', ${item.product_id}, 'no_change')">Va bene cosi', nessuna modifica</button>
        ${confirmButton}
      </div>
    `;
    grid.appendChild(card);
    card.querySelector(`#${fileInputId}`).addEventListener("change", (e) => {
      if (e.target.files[0]) setCustomImage(currentCategory, item.product_id, e.target.files[0]);
    });
  });

  document.getElementById("stats").innerText =
    `Categoria: ${currentCategory} | Totale: ${currentItems.length} | Foto proprie caricate: ${custom} | Nessuna modifica: ${noChange} | Da rivedere: ${pending}`;
}

function exportResults() {
  const result = {};
  for (let i = 0; i < localStorage.length; i++) {
    const key = localStorage.key(i);
    if (!key.startsWith("review_")) continue;
    const rest = key.substring("review_".length);
    const lastUnderscore = rest.lastIndexOf("_");
    const category = rest.substring(0, lastUnderscore);
    const productId = rest.substring(lastUnderscore + 1);
    if (!result[category]) result[category] = {};
    const raw = localStorage.getItem(key);
    let data;
    try { data = JSON.parse(raw); } catch (e) { data = { status: raw }; }
    result[category][productId] = data;
  }
  const blob = new Blob([JSON.stringify(result, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  const now = new Date().toISOString().slice(0, 10);
  a.href = url;
  a.download = `revisione_${now}.json`;
  a.click();
  URL.revokeObjectURL(url);
}

function importResults(event) {
  const file = event.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    try {
      const data = JSON.parse(reader.result);
      Object.entries(data).forEach(([category, items]) => {
        Object.entries(items).forEach(([productId, value]) => {
          const normalized = typeof value === "string" ? { status: value } : value;
          saveReviewData(category, productId, normalized);
        });
      });
      alert("File importato correttamente.");
      renderGrid();
    } catch (e) {
      alert("Il file selezionato non e' valido.");
    }
  };
  reader.readAsText(file);
}

init();
"""

GUIDA_HTML = """<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Guida - Revisione foto prodotti</title>
<style>
  body { font-family: Arial, Helvetica, sans-serif; max-width: 700px; margin: 40px auto; padding: 0 20px; line-height: 1.6; color: #222; }
  h1 { font-size: 24px; }
  h2 { font-size: 18px; margin-top: 32px; color: #1565c0; }
  .step { background: #f5f5f5; border-radius: 8px; padding: 16px; margin-bottom: 16px; }
  .btn-example { display: inline-block; padding: 6px 14px; border-radius: 4px; color: white; font-size: 14px; margin: 0 4px; }
  .approve-ex { background: #2e7d32; }
  .reject-ex { background: #c62828; }
  a.back { display: inline-block; margin-bottom: 20px; color: #1565c0; }
</style>
</head>
<body>
  <a class="back" href="index.html">&larr; Torna alle foto</a>
  <h1>Guida rapida: come controllare le foto</h1>
  <p>Non serve installare nulla ed essere esperti di computer: basta questa pagina.</p>

  <div class="step">
    <h2>1. Guarda ogni prodotto</h2>
    <p>Per ogni prodotto vedi la foto <b>attuale</b> (quella di adesso sul sito).</p>
  </div>

  <div class="step">
    <h2>2. Scegli cosa fare</h2>
    <p>Sotto la foto trovi due possibilita':</p>
    <p>
      <span class="btn-example approve-ex">Va bene cosi', nessuna modifica</span> se la foto attuale ti piace<br><br>
      <b>"Carica una tua foto"</b> se vuoi sostituirla con una foto tua: appena la carichi viene
      pubblicata subito sul sito, senza bisogno di fare altro.
    </p>
    <p>Vai con calma, non c'e' fretta. Puoi anche chiudere la pagina e tornare piu' tardi:
    le tue scelte restano salvate (se usi sempre lo stesso computer e browser).</p>
  </div>

  <div class="step">
    <h2>3. Vuoi solo guardarla con calma?</h2>
    <p>Il link <b>"Scarica foto attuale"</b> ti salva sul computer/telefono la foto di adesso,
    cosi' la puoi guardare con calma o mandarla a chi vuoi, prima di decidere se sostituirla.</p>
  </div>

  <div class="step">
    <h2>4. Quando hai finito tutte le categorie</h2>
    <p>Clicca il pulsante verde <b>"Scarica risultati"</b> in alto nella pagina.
    Si scarichera' un piccolo file. Invialo a Marco su WhatsApp o email, come fai di solito.</p>
  </div>

  <h2>Domande frequenti</h2>
  <p><b>Ho sbagliato a caricare una foto, posso cambiare idea?</b><br>
  Si', carica di nuovo una foto per lo stesso prodotto: sostituisce quella appena pubblicata.</p>

  <p><b>Non sono sicura su un prodotto, cosa faccio?</b><br>
  Lascialo senza cliccare nulla e chiedi a Marco prima di decidere.</p>

  <p><b>Ho chiuso la pagina senza scaricare il file, ho perso tutto?</b><br>
  No, se riapri il link dallo stesso computer le tue scelte sono ancora li'.
  Ricordati pero' di scaricare e inviare il file quando hai finito.</p>
</body>
</html>
"""


def main():
    categories = sys.argv[1:] or [
        p.name for p in BACKUP_ROOT.iterdir()
        if p.is_dir() and (p / "manifest" / "manifest.json").exists()
    ]
    built = []
    for slug in categories:
        result = build_category(slug)
        if result:
            built.append(result)

    write_index_html()
    print(f"\nSito statico generato in: {DOCS_ROOT}")
    print(f"Categorie incluse: {built}")


if __name__ == "__main__":
    main()
