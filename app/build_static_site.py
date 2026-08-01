"""
Genera il sito statico di revisione per GitHub Pages in docs/.
Legge processed/<categoria>/process_manifest.json e copia le immagini
(originale + risultato) ridimensionate in docs/assets/<categoria>/.

La cliente approva/rifiuta ogni prodotto: la scelta viene salvata SOLO nel suo
browser (localStorage), perche' GitHub Pages e' hosting statico e non ha un
server dietro. A fine revisione clicca "Scarica risultati" e invia il file
JSON scaricato a Marco, che lo importa con app/import_reviews.py per sapere
quali prodotti rielaborare.

Uso:
  python app/build_static_site.py                     # tutte le categorie in processed/
  python app/build_static_site.py color-gel acrygel    # solo alcune categorie
"""
import json
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
PROCESSED_ROOT = ROOT / "processed"
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
    manifest_path = PROCESSED_ROOT / category_slug / "process_manifest.json"
    if not manifest_path.exists():
        print(f"[{category_slug}] Nessun manifest elaborato trovato, salto.")
        return None

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    items = []

    orig_dir = ASSETS_ROOT / category_slug / "original"
    proc_dir = ASSETS_ROOT / category_slug / "processed"

    for entry in manifest:
        if entry.get("process_status") != "ok":
            continue
        pid = entry["product_id"]

        src_original = ROOT / entry["backup_local_path"]
        src_processed = ROOT / entry["processed_path"]

        original_web_name = f"{pid}.jpg"
        processed_web_name = f"{pid}.jpg"

        resize_for_web(src_original, orig_dir / original_web_name)
        resize_for_web(src_processed, proc_dir / processed_web_name)

        items.append({
            "product_id": pid,
            "name": entry["name"],
            "original_image": f"assets/{category_slug}/original/{original_web_name}",
            "processed_image": f"assets/{category_slug}/processed/{processed_web_name}",
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

  <script src="app.js"></script>
</body>
</html>
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
.approve { background: #2e7d32; color: white; }
.approve.active { outline: 3px solid #a5d6a7; }
.reject { background: #c62828; color: white; }
.reject.active { outline: 3px solid #ef9a9a; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; margin-left: 6px; }
.badge.approved { background: #c8e6c9; color: #256029; }
.badge.rejected { background: #ffcdd2; color: #b71c1c; }
.badge.pending { background: #eeeeee; color: #555; }
.badge.custom { background: #bbdefb; color: #0d47a1; }

.secondary-actions { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-top: 10px; font-size: 12px; }
.download-link { color: #1565c0; text-decoration: none; border: 1px solid #1565c0; padding: 6px 10px; border-radius: 4px; }
.download-link:hover { background: #e3f2fd; }
.upload-label { color: #0d47a1; border: 1px solid #0d47a1; padding: 6px 10px; border-radius: 4px; cursor: pointer; background: #e3f2fd; }
.upload-label:hover { background: #bbdefb; }
"""

APP_JS = """
const CATEGORIES_HINT = ["color-gel", "acrygel", "semi-permanente"];
let currentCategory = null;
let currentItems = [];

function reviewKey(category, productId) {
  return `review_${category}_${productId}`;
}

function getReviewData(category, productId) {
  const raw = localStorage.getItem(reviewKey(category, productId));
  if (!raw) return { status: "pending" };
  try {
    return JSON.parse(raw);
  } catch (e) {
    return { status: raw }; // compatibilita' con vecchio formato (solo stringa)
  }
}

function saveReviewData(category, productId, data) {
  localStorage.setItem(reviewKey(category, productId), JSON.stringify(data));
}

function setReview(category, productId, status) {
  const data = getReviewData(category, productId);
  data.status = status;
  saveReviewData(category, productId, data);
  renderGrid();
}

function setCustomImage(category, productId, file) {
  const reader = new FileReader();
  reader.onload = () => {
    const data = getReviewData(category, productId);
    data.status = "custom";
    data.customImage = reader.result; // data URL base64
    data.customImageName = file.name;
    saveReviewData(category, productId, data);
    renderGrid();
  };
  reader.readAsDataURL(file);
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
  let approved = 0, rejected = 0, pending = 0;

  let custom = 0;
  currentItems.forEach(item => {
    const data = getReviewData(currentCategory, item.product_id);
    const status = data.status || "pending";
    if (status === "approved") approved++;
    else if (status === "rejected") rejected++;
    else if (status === "custom") custom++;
    else pending++;

    const fileInputId = `custom-${currentCategory}-${item.product_id}`;
    const customPreview = status === "custom" && data.customImage
      ? `<figure><img src="${data.customImage}" alt="tua foto"><figcaption>La tua foto (${data.customImageName || ""})</figcaption></figure>`
      : "";

    const card = document.createElement("div");
    card.className = "card";
    card.innerHTML = `
      <div class="name">${item.name} <span class="badge ${status}">${status}</span></div>
      <div class="images">
        <figure>
          <img src="${item.original_image}" alt="originale">
          <figcaption>Attuale</figcaption>
        </figure>
        <figure>
          <img src="${item.processed_image}" alt="nuova">
          <figcaption>Nuova proposta</figcaption>
        </figure>
        ${customPreview}
      </div>
      <div class="secondary-actions">
        <a class="download-link" href="${item.original_image}" download="attuale_${item.product_id}.jpg">Scarica foto attuale</a>
        <label class="upload-label" for="${fileInputId}">Carica una tua foto</label>
        <input type="file" id="${fileInputId}" accept="image/*" style="display:none">
      </div>
      <div class="actions">
        <button class="act approve ${status === 'approved' ? 'active' : ''}"
          onclick="setReview('${currentCategory}', ${item.product_id}, 'approved')">Approva</button>
        <button class="act reject ${status === 'rejected' ? 'active' : ''}"
          onclick="setReview('${currentCategory}', ${item.product_id}, 'rejected')">Rifiuta</button>
      </div>
    `;
    grid.appendChild(card);
    card.querySelector(`#${fileInputId}`).addEventListener("change", (e) => {
      if (e.target.files[0]) setCustomImage(currentCategory, item.product_id, e.target.files[0]);
    });
  });

  document.getElementById("stats").innerText =
    `Categoria: ${currentCategory} | Totale: ${currentItems.length} | Approvate: ${approved} | Rifiutate: ${rejected} | Foto proprie: ${custom} | Da rivedere: ${pending}`;
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
    <p>Per ogni prodotto vedi due foto affiancate: a sinistra quella <b>attuale</b> (di adesso),
    a destra quella <b>nuova</b> proposta.</p>
  </div>

  <div class="step">
    <h2>2. Scegli</h2>
    <p>Sotto ogni foto trovi due pulsanti:</p>
    <p>
      <span class="btn-example approve-ex">Approva</span> la nuova foto va bene<br><br>
      <span class="btn-example reject-ex">Rifiuta</span> la nuova foto NON va bene, va rifatta
    </p>
    <p>Vai con calma, non c'e' fretta. Puoi anche chiudere la pagina e tornare piu' tardi:
    le tue scelte restano salvate (se usi sempre lo stesso computer e browser).</p>
  </div>

  <div class="step">
    <h2>3. Non ti piace nessuna delle due foto?</h2>
    <p>Per ogni prodotto trovi anche due link piu' piccoli:</p>
    <p>
      <b>"Scarica foto attuale"</b> ti salva sul computer/telefono la foto di adesso,
      cosi' la puoi guardare con calma o mandarla a chi vuoi.<br><br>
      <b>"Carica una tua foto"</b> ti permette di scegliere TU una foto dal tuo computer/telefono
      per quel prodotto (ad esempio se ne hai una che ti piace di piu'). Basta cliccare e scegliere
      il file: comparira' subito come anteprima nella scheda del prodotto.
    </p>
  </div>

  <div class="step">
    <h2>4. Quando hai finito tutte le categorie</h2>
    <p>Clicca il pulsante verde <b>"Scarica risultati"</b> in alto nella pagina.
    Si scarichera' un piccolo file. Invialo a Marco su WhatsApp o email, come fai di solito.</p>
  </div>

  <h2>Domande frequenti</h2>
  <p><b>Ho sbagliato a cliccare, posso cambiare idea?</b><br>
  Si', clicca di nuovo sull'altro pulsante per lo stesso prodotto: la scelta si aggiorna subito.</p>

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
        p.name for p in PROCESSED_ROOT.iterdir()
        if p.is_dir() and (p / "process_manifest.json").exists()
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
