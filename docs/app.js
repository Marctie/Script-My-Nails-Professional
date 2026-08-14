
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

// Manda la scelta della cliente a un Cloudflare Worker (WORKER_URL, vedi
// config.js), che scrive il file nella cartella queue/ del repo GitHub al
// posto del sito: il token GitHub reale resta nascosto lato Worker, non e'
// mai nel sorgente pubblico della pagina, quindi GitHub non lo revoca piu'
// automaticamente. Termux (live_server.py) fa poi polling sulla coda ed
// elabora/cancella la richiesta, esattamente come prima.
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

  try {
    const res = await fetch(WORKER_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const latest = getReviewData(category, productId);
    latest.publishState = res.ok
      ? "inviato (verra' pubblicato a breve)"
      : `errore invio: HTTP ${res.status}`;
    saveReviewData(category, productId, latest);
  } catch (e) {
    const latest = getReviewData(category, productId);
    latest.publishState = "servizio non raggiungibile (salvato solo qui)";
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
