/* Sessione e login dell'editor (condiviso da index, home, sito, foto). Vedi editor_sito/sessions.py per il lato telefono.
   - Il codice NON viaggia mai: dal codice si ricava una chiave (PBKDF2) e ogni richiesta e' firmata con HMAC.
   - Il controllo "codice giusto?" nel browser usa solo l'impronta pubblica (utenti.js); la vera protezione e' sul telefono,
     che accetta modifiche solo con firma valida E sessione attiva.
   - La chiave resta solo in sessionStorage (sparisce chiudendo la scheda). */
(function () {
  "use strict";
  const CFG = window.MNP_UTENTI || {users: {}, session: {maxActions: 10, idleMin: 30}};
  const MAX_ACTIONS = CFG.session.maxActions, IDLE_S = CFG.session.idleMin * 60;
  const SK = "mnp_session", ITER = 600000;
  const enc = new TextEncoder();
  const hex = b => [...new Uint8Array(b)].map(x => x.toString(16).padStart(2, "0")).join("");
  const now = () => Math.floor(Date.now() / 1000);

  async function sha256Hex(s) { return hex(await crypto.subtle.digest("SHA-256", enc.encode(s))); }
  async function deriveKey(code, uid, iter) {
    const base = await crypto.subtle.importKey("raw", enc.encode(code), "PBKDF2", false, ["deriveBits"]);
    const bits = await crypto.subtle.deriveBits({name: "PBKDF2", hash: "SHA-256", salt: enc.encode("mnp-editor|" + uid), iterations: iter || ITER}, base, 256);
    return hex(bits);
  }
  const verifierOf = key => sha256Hex("verify|" + key);
  function canon(v) {
    if (Array.isArray(v)) return "[" + v.map(canon).join(",") + "]";
    if (v && typeof v === "object") return "{" + Object.keys(v).sort().filter(k => v[k] !== undefined).map(k => JSON.stringify(k) + ":" + canon(v[k])).join(",") + "}";
    return JSON.stringify(v);
  }
  function signable(o) {
    const c = Object.assign({}, o);
    delete c.image_base64; delete c.image_mime;
    if (Array.isArray(c.images)) c.images = c.images.map(i => { const d = Object.assign({}, i); delete d.image_base64; delete d.image_mime; return d; });
    return c;
  }
  async function hmacHex(keyHex, message) {
    const k = await crypto.subtle.importKey("raw", enc.encode(keyHex), {name: "HMAC", hash: "SHA-256"}, false, ["sign"]);
    return hex(await crypto.subtle.sign("HMAC", k, enc.encode(message)));
  }
  const signOf = (keyHex, user, ts, obj) => hmacHex(keyHex, `${user}|${ts}|${canon(signable(obj))}`);

  // ------------------------------------------------------------ sessione locale
  function read() { try { return JSON.parse(sessionStorage.getItem(SK) || "null"); } catch (e) { return null; } }
  function write(s) { try { sessionStorage.setItem(SK, JSON.stringify(s)); } catch (e) {} }
  function get() {
    const s = read();
    if (!s) return null;
    if (now() - s.last > IDLE_S || s.actions >= MAX_ACTIONS) return null;
    return s;
  }
  let lastTouch = 0;
  function touch(force) {
    const s = read(); if (!s) return;
    if (!force && Date.now() - lastTouch < 15000) return;
    lastTouch = Date.now(); s.last = now(); write(s);
  }

  // ------------------------------------------------------------ rete
  /* Il Worker Cloudflare inoltra al telefono SOLO category, product_id, status, image_base64 e image_mime (scarta ogni altro campo).
     L'intera richiesta firmata viaggia quindi DENTRO image_base64 con un tipo speciale: il telefono la riapre (live_server.unwrap_tunnel).
     La firma e' dentro la richiesta, quindi la sicurezza non cambia. */
  const TUNNEL_MIME = "application/x-mnp+json";
  function b64utf8(str) {
    const bytes = new TextEncoder().encode(str); let bin = "";
    for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    return btoa(bin);
  }
  function wrap(payload) {
    if (payload.image_mime === TUNNEL_MIME) return payload;
    return {category: payload.category, product_id: payload.product_id, status: "custom", image_base64: b64utf8(JSON.stringify(payload)), image_mime: TUNNEL_MIME};
  }
  async function post(payload) {
    if (typeof WORKER_URL === "undefined" || !WORKER_URL) return false;
    for (let i = 0; i < 3; i++) {
      try {
        const r = await fetch(WORKER_URL, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(wrap(payload))});
        if (r.ok) return true;
      } catch (e) {}
      await new Promise(r => setTimeout(r, 1500 * (i + 1)));
    }
    return false;
  }
  /* Costruisce un payload firmato: base = campi in chiaro (category, product_id, status, + il campo con la richiesta);
     obj = l'oggetto che la firma protegge (per site_edit/home_hero/session_event e' il campo stesso). */
  async function buildPayload(base, obj) {
    const s = get(); if (!s) throw new Error("sessione scaduta");
    const ts = now();
    return Object.assign({}, base, {user: s.user, ts, sig: await signOf(s.key, s.user, ts, obj)});
  }
  async function sendEvent(ev) {
    return post(await buildPayload({category: "session", product_id: "event", status: "custom", session_event: ev}, ev));
  }

  // ------------------------------------------------------------ login / logout
  function deviceInfo() {   // es. "Android · Chrome · 390x800" (leggibile nel messaggio Telegram)
    const ua = navigator.userAgent || "";
    const os = /Android/i.test(ua) ? "Android" : /iPhone|iPad|iPod/i.test(ua) ? "iPhone/iPad" : /Windows/i.test(ua) ? "Windows" : /Mac OS/i.test(ua) ? "Mac" : /Linux/i.test(ua) ? "Linux" : "sistema sconosciuto";
    const br = /Edg\//i.test(ua) ? "Edge" : /Firefox/i.test(ua) ? "Firefox" : /SamsungBrowser/i.test(ua) ? "Samsung Internet" : /Chrome|CriOS/i.test(ua) ? "Chrome" : /Safari/i.test(ua) ? "Safari" : "browser sconosciuto";
    return os + " · " + br + " · " + innerWidth + "x" + innerHeight;
  }
  async function login(uid, code) {
    const u = CFG.users[uid];
    if (!u) throw new Error("utente");
    const key = await deriveKey(code, uid);
    if ((await verifierOf(key)) !== u.verifier) {
      post({category: "session", product_id: "event", status: "custom", session_event: {type: "login_failed", user_try: uid, device: deviceInfo()}});
      throw new Error("codice");
    }
    write({user: uid, name: u.name, key, started: now(), last: now(), actions: 0});
    await sendEvent({type: "login", device: deviceInfo()});
    return get();
  }
  async function logout(reason) {
    const s = get();
    if (s) { try { await sendEvent({type: "logout", reason: reason || "uscita volontaria", actions: s.actions}); } catch (e) {} }
    try { sessionStorage.removeItem(SK); } catch (e) {}
  }
  /* Da chiamare dopo OGNI modifica andata a buon fine. Ritorna {count, max, closed}. */
  async function countAction() {
    const s = read(); if (!s) return {count: 0, max: MAX_ACTIONS, closed: true};
    s.actions += 1; s.last = now(); write(s);
    refreshBar();
    if (s.actions >= MAX_ACTIONS) {
      // il telefono chiude la sessione da solo al raggiungimento del limite: qui si pulisce e si torna all'ingresso
      try { sessionStorage.removeItem(SK); } catch (e) {}
      return {count: s.actions, max: MAX_ACTIONS, closed: true};
    }
    return {count: s.actions, max: MAX_ACTIONS, closed: false};
  }

  // ------------------------------------------------------------ pagine interne
  function toHub(msg) { location.replace("index.html" + (msg ? "?msg=" + msg : "")); }
  function guard() {
    const s = get();
    if (!s) { toHub(read() ? "scaduta" : ""); return null; }
    setInterval(() => { if (!get()) toHub("scaduta"); }, 20000);
    ["click", "keydown", "input", "touchstart"].forEach(e => document.addEventListener(e, () => touch(false), {passive: true}));
    return s;
  }
  let barEl = null;
  function refreshBar() {
    if (!barEl) return;
    const s = read();
    if (!s) return;
    barEl.querySelector(".mnp-who").textContent = "👤 " + s.name;
    barEl.querySelector(".mnp-cnt").textContent = "Modifiche: " + s.actions + " / " + MAX_ACTIONS;
  }
  function renderBar(host) {
    if (!document.getElementById("mnp-bar-css")) {
      const st = document.createElement("style"); st.id = "mnp-bar-css";
      st.textContent = ".mnp-bar{display:flex;gap:10px;align-items:center;justify-content:space-between;flex-wrap:wrap;font:500 15px/1.3 Jost,system-ui,sans-serif;border:1px solid #E0D4C8;border-radius:12px;padding:8px 12px;margin:0 0 14px;background:rgba(180,137,124,.12)}"
        + ".mnp-bar a,.mnp-bar button{font:inherit;color:inherit;background:none;border:1px solid #E0D4C8;border-radius:8px;padding:5px 10px;cursor:pointer;text-decoration:none}"
        + ".mnp-bar .mnp-r{display:flex;gap:8px;align-items:center;flex-wrap:wrap}";
      document.head.appendChild(st);
    }
    barEl = document.createElement("div"); barEl.className = "mnp-bar";
    barEl.innerHTML = '<span class="mnp-who"></span><span class="mnp-r"><span class="mnp-cnt"></span><a href="index.html">Menu</a><button type="button" class="mnp-out">Esci</button></span>';
    barEl.querySelector(".mnp-out").onclick = async () => { barEl.querySelector(".mnp-out").disabled = true; await logout("uscita volontaria"); toHub("uscito"); };
    host.insertBefore(barEl, host.firstChild);
    refreshBar();
  }

  window.MNP = {get, login, logout, guard, renderBar, countAction, buildPayload, post, sendEvent, touch, wrap, TUNNEL_MIME,
    MAX_ACTIONS, IDLE_S, users: CFG.users, _deriveKey: deriveKey, _verifier: verifierOf, _sign: signOf, _canon: canon};
})();
