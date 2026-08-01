"""
Dashboard locale di revisione: mostra per ogni prodotto l'immagine originale
affiancata al risultato elaborato, con stato di avanzamento e pulsanti
Approva / Rifiuta. Solo le immagini APPROVATE verranno caricate su WooCommerce
dallo script upload.py.

Avvio: python app/dashboard.py
Poi apri: http://127.0.0.1:5000
"""
import json
from pathlib import Path

from flask import Flask, jsonify, request, send_file, render_template_string

ROOT = Path(__file__).resolve().parent.parent
PROCESS_MANIFEST = ROOT / "processed" / "process_manifest.json"
REVIEW_STATE_PATH = ROOT / "processed" / "review_state.json"

app = Flask(__name__)

TEMPLATE = """
<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8">
<title>Revisione immagini - Color Gel</title>
<style>
  body { font-family: Arial, sans-serif; background: #f5f5f5; margin: 0; padding: 20px; }
  h1 { font-size: 20px; }
  .stats { margin-bottom: 20px; font-size: 14px; color: #444; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(420px, 1fr)); gap: 16px; }
  .card { background: white; border-radius: 8px; padding: 12px; box-shadow: 0 1px 3px rgba(0,0,0,0.15); }
  .card img { width: 100%; border: 1px solid #ddd; border-radius: 4px; }
  .name { font-weight: bold; margin: 8px 0 4px; font-size: 14px; }
  .actions { margin-top: 8px; display: flex; gap: 8px; }
  button { flex: 1; padding: 8px; border: none; border-radius: 4px; cursor: pointer; font-size: 13px; }
  .approve { background: #2e7d32; color: white; }
  .reject { background: #c62828; color: white; }
  .pending { background: #eee; color: #333; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; margin-left: 6px; }
  .badge.approved { background: #c8e6c9; color: #256029; }
  .badge.rejected { background: #ffcdd2; color: #b71c1c; }
  .badge.pending { background: #eeeeee; color: #555; }
</style>
</head>
<body>
  <h1>Revisione immagini - Color Gel</h1>
  <div class="stats" id="stats"></div>
  <div class="grid" id="grid"></div>

<script>
async function load() {
  const res = await fetch('/api/items');
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
      <img src="/preview/${item.product_id}" alt="preview">
      <div class="actions">
        <button class="approve" onclick="setReview(${item.product_id}, 'approved')">Approva</button>
        <button class="reject" onclick="setReview(${item.product_id}, 'rejected')">Rifiuta</button>
      </div>
    `;
    grid.appendChild(card);
  });
  document.getElementById('stats').innerText =
    `Totale: ${data.length} | Approvate: ${approved} | Rifiutate: ${rejected} | Da rivedere: ${pending}`;
}

async function setReview(productId, status) {
  await fetch('/api/review', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({product_id: productId, review: status})
  });
  load();
}

load();
</script>
</body>
</html>
"""


def load_manifest():
    return json.loads(PROCESS_MANIFEST.read_text(encoding="utf-8"))


def load_review_state():
    if REVIEW_STATE_PATH.exists():
        return json.loads(REVIEW_STATE_PATH.read_text(encoding="utf-8"))
    return {}


def save_review_state(state):
    REVIEW_STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


@app.route("/")
def index():
    return render_template_string(TEMPLATE)


@app.route("/api/items")
def api_items():
    manifest = load_manifest()
    review_state = load_review_state()
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
    pid = str(data["product_id"])
    state = load_review_state()
    state[pid] = data["review"]
    save_review_state(state)
    return jsonify({"ok": True})


@app.route("/preview/<int:product_id>")
def preview(product_id):
    manifest = load_manifest()
    entry = next((e for e in manifest if e["product_id"] == product_id), None)
    if entry is None:
        return "Non trovato", 404
    preview_path = ROOT / "processed" / "preview" / (Path(entry["processed_path"]).stem + ".png")
    return send_file(preview_path)


if __name__ == "__main__":
    app.run(debug=True, port=5000)
