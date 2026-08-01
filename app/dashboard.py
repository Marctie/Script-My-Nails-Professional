"""
Dashboard locale di revisione (per te, in locale): mostra per ogni prodotto,
raggruppato per categoria, l'immagine originale affiancata al risultato
elaborato, con stato di avanzamento e pulsanti Approva / Rifiuta. Solo le
immagini APPROVATE verranno caricate su WooCommerce dallo script upload.py.

Avvio: python app/dashboard.py
Poi apri: http://127.0.0.1:5000
"""
import json
from pathlib import Path

from flask import Flask, jsonify, request, send_file, render_template_string

ROOT = Path(__file__).resolve().parent.parent
PROCESSED_ROOT = ROOT / "processed"

app = Flask(__name__)

TEMPLATE = """
<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8">
<title>Revisione immagini prodotto</title>
<style>
  body { font-family: Arial, sans-serif; background: #f5f5f5; margin: 0; padding: 20px; }
  h1 { font-size: 20px; }
  .tabs { display: flex; gap: 8px; margin-bottom: 16px; }
  .tabs button { padding: 8px 16px; border: 1px solid #ccc; background: white; border-radius: 6px; cursor: pointer; }
  .tabs button.active { background: #1565c0; color: white; border-color: #1565c0; }
  .stats { margin-bottom: 20px; font-size: 14px; color: #444; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(420px, 1fr)); gap: 16px; }
  .card { background: white; border-radius: 8px; padding: 12px; box-shadow: 0 1px 3px rgba(0,0,0,0.15); }
  .card img { width: 100%; border: 1px solid #ddd; border-radius: 4px; }
  .name { font-weight: bold; margin: 8px 0 4px; font-size: 14px; }
  .actions { margin-top: 8px; display: flex; gap: 8px; }
  button.act { flex: 1; padding: 8px; border: none; border-radius: 4px; cursor: pointer; font-size: 13px; }
  .approve { background: #2e7d32; color: white; }
  .reject { background: #c62828; color: white; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; margin-left: 6px; }
  .badge.approved { background: #c8e6c9; color: #256029; }
  .badge.rejected { background: #ffcdd2; color: #b71c1c; }
  .badge.pending { background: #eeeeee; color: #555; }
</style>
</head>
<body>
  <h1>Revisione immagini prodotto</h1>
  <div class="tabs" id="tabs"></div>
  <div class="stats" id="stats"></div>
  <div class="grid" id="grid"></div>

<script>
let currentCategory = null;

async function loadCategories() {
  const res = await fetch('/api/categories');
  const categories = await res.json();
  const tabs = document.getElementById('tabs');
  tabs.innerHTML = '';
  categories.forEach(cat => {
    const btn = document.createElement('button');
    btn.textContent = cat;
    btn.onclick = () => selectCategory(cat);
    btn.dataset.cat = cat;
    tabs.appendChild(btn);
  });
  if (categories.length) selectCategory(categories[0]);
}

function selectCategory(cat) {
  currentCategory = cat;
  document.querySelectorAll('#tabs button').forEach(b => {
    b.classList.toggle('active', b.dataset.cat === cat);
  });
  load();
}

async function load() {
  const res = await fetch(`/api/items?category=${encodeURIComponent(currentCategory)}`);
  const data = await res.json();
  const grid = document.getElementById('grid');
  grid.innerHTML = '';
  let approved = 0, rejected = 0, pending = 0;
  data.forEach(item => {
    if (item.review === 'approved') approved++;
    else if (item.review === 'rejected') rejected++;
    else pending++;

    const card = document.createElement('div');
    card.className = 'card';
    card.innerHTML = `
      <div class="name">${item.name} <span class="badge ${item.review}">${item.review}</span></div>
      <img src="/preview/${currentCategory}/${item.product_id}" alt="preview">
      <div class="actions">
        <button class="act approve" onclick="setReview('${currentCategory}', ${item.product_id}, 'approved')">Approva</button>
        <button class="act reject" onclick="setReview('${currentCategory}', ${item.product_id}, 'rejected')">Rifiuta</button>
      </div>
    `;
    grid.appendChild(card);
  });
  document.getElementById('stats').innerText =
    `Categoria: ${currentCategory} | Totale: ${data.length} | Approvate: ${approved} | Rifiutate: ${rejected} | Da rivedere: ${pending}`;
}

async function setReview(category, productId, status) {
  await fetch('/api/review', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({category, product_id: productId, review: status})
  });
  load();
}

loadCategories();
</script>
</body>
</html>
"""


def list_categories():
    if not PROCESSED_ROOT.exists():
        return []
    return sorted(
        p.name for p in PROCESSED_ROOT.iterdir()
        if p.is_dir() and (p / "process_manifest.json").exists()
    )


def load_manifest(category: str):
    path = PROCESSED_ROOT / category / "process_manifest.json"
    return json.loads(path.read_text(encoding="utf-8"))


def review_state_path(category: str) -> Path:
    return PROCESSED_ROOT / category / "review_state.json"


def load_review_state(category: str):
    path = review_state_path(category)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def save_review_state(category: str, state: dict):
    review_state_path(category).write_text(
        json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8"
    )


@app.route("/")
def index():
    return render_template_string(TEMPLATE)


@app.route("/api/categories")
def api_categories():
    return jsonify(list_categories())


@app.route("/api/items")
def api_items():
    category = request.args.get("category")
    manifest = load_manifest(category)
    review_state = load_review_state(category)
    items = []
    for entry in manifest:
        if entry.get("process_status") != "ok":
            continue
        pid = str(entry["product_id"])
        items.append({
            "product_id": entry["product_id"],
            "name": entry["name"],
            "review": review_state.get(pid, "pending"),
        })
    return jsonify(items)


@app.route("/api/review", methods=["POST"])
def api_review():
    data = request.get_json()
    category = data["category"]
    pid = str(data["product_id"])
    state = load_review_state(category)
    state[pid] = data["review"]
    save_review_state(category, state)
    return jsonify({"ok": True})


@app.route("/preview/<category>/<int:product_id>")
def preview(category, product_id):
    manifest = load_manifest(category)
    entry = next((e for e in manifest if e["product_id"] == product_id), None)
    if entry is None:
        return "Non trovato", 404
    preview_path = ROOT / "processed" / category / "preview" / (Path(entry["processed_path"]).stem + ".png")
    return send_file(preview_path)


if __name__ == "__main__":
    app.run(debug=True, port=5000)
