
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

function dataUrlToBlob(dataUrl) {
  const [header, b64] = dataUrl.split(",", 2);
  const mime = (header.match(/data:(.*?);base64/) || [])[1] || "image/jpeg";
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return new Blob([bytes], { type: mime });
}

// Manda la scelta della cliente direttamente all'API pubblica di Telegram
// (bot dedicato in ascolto su Termux tramite polling, nessun server esposto
// su internet). Il caption/testo segue il formato che live_server.py si
// aspetta: MYNAILS|<token>|<categoria>|<product_id>|<stato>
async function submitToLiveServer(category, productId, status, customImage) {
  if (!TELEGRAM_BOT_TOKEN || !TELEGRAM_CHAT_ID) return; // non configurato: resta solo salvataggio locale

  const data = getReviewData(category, productId);
  data.publishState = "invio in corso...";
  saveReviewData(category, productId, data);
  renderGrid();

  const caption = `MYNAILS|${REVIEW_TOKEN || ""}|${category}|${productId}|${status}`;
  const apiBase = `https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}`;

  try {
    let res;
    if (status === "custom" && customImage) {
      const form = new FormData();
      form.append("chat_id", TELEGRAM_CHAT_ID);
      form.append("caption", caption);
      form.append("document", dataUrlToBlob(customImage), `${productId}.jpg`);
      res = await fetch(`${apiBase}/sendDocument`, { method: "POST", body: form });
    } else {
      res = await fetch(`${apiBase}/sendMessage`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chat_id: TELEGRAM_CHAT_ID, text: caption }),
      });
    }
    const result = await res.json();
    const latest = getReviewData(category, productId);
    latest.publishState = result.ok
      ? "inviato al bot (verra' pubblicato a breve)"
      : `errore invio: ${result.description || "sconosciuto"}`;
    saveReviewData(category, productId, latest);
  } catch (e) {
    const latest = getReviewData(category, productId);
    latest.publishState = "Telegram non raggiungibile (salvato solo qui)";
    saveReviewData(category, productId, latest);
  }
  renderGrid();
}

function setReview(category, productId, status) {
  const data = getReviewData(category, productId);
  data.status = status;
  saveReviewData(category, productId, data);
  renderGrid();
  submitToLiveServer(category, productId, status, data.customImage);
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
    submitToLiveServer(category, productId, "custom", data.customImage);
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
