/* Swath frontend. Plain JS + Leaflet. State lives in S; every list re-renders from it. */
"use strict";
const S = { settings: null, customers: [], fields: [], sensitive: [], pilots: [], aircraft: [], jobs: [], now: null, windows: {}, mock: false };
let map, fieldLayer, sensLayer, routeLayer, previewLayer, drawHandler = null, drawKind = null, pickBase = false;

// ---------- helpers ----------
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const STATUS_TXT = { go: "GO", caution: "CAUTION", nogo: "NO-GO" };
const STATUS_COLOR = { go: "#1e8e3e", caution: "#e89a1a", nogo: "#c5221f", none: "#8a948e" };
const PRIORITY = { 1: "Normal", 2: "High", 3: "Urgent" };
const RATE_UNITS = ["fl oz/ac", "oz/ac", "pt/ac", "qt/ac", "gal/ac", "lb/ac"];
const SENS_KINDS = [["apiary", "Apiary / bees"], ["organic", "Organic crop"], ["susceptible", "Susceptible crop"], ["residence", "Home / yard"],
  ["school", "School / public"], ["water", "Water"], ["livestock", "Livestock"], ["other", "Other"]];
const COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];

async function api(method, url, body) {
  const opt = { method, headers: {} };
  if (body !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  const r = await fetch(url, opt);
  if (r.status === 401) { location.href = "/login"; throw new Error("Sign in first"); }
  let data = null;
  try { data = await r.json(); } catch (e) { /* empty body */ }
  if (!r.ok) throw new Error((data && data.error) || `${r.status} ${r.statusText}`);
  return data;
}
function toast(msg, ms = 2600) {
  const t = $("toast"); t.textContent = msg; t.classList.remove("hidden");
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.add("hidden"), ms);
}
const tz = () => (S.settings && S.settings.tz) || "America/Chicago";
const fmtTime = (ts) => new Date(ts * 1000).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", timeZone: tz() });
const fmtDay = (ts) => new Date(ts * 1000).toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric", timeZone: tz() });
const localDate = (ts) => new Date(ts * 1000).toLocaleDateString("en-CA", { timeZone: tz() });   // YYYY-MM-DD
const localHour = (ts) => parseInt(new Date(ts * 1000).toLocaleString("en-US", { hour: "numeric", hourCycle: "h23", timeZone: tz() }), 10);
const hhmm12 = (m) => { m = Math.round(m); const h = Math.floor(m / 60) % 24, mm = m % 60; return `${(h % 12) || 12}:${String(mm).padStart(2, "0")} ${h < 12 ? "AM" : "PM"}`; };
const dur = (min) => { min = Math.round(min); const h = Math.floor(min / 60), m = min % 60; return h ? `${h} h ${m ? m + " min" : ""}`.trim() : `${m} min`; };
const compass = (d) => COMPASS[Math.round(((d % 360) + 360) % 360 / 22.5) % 16];
const fieldById = (id) => S.fields.find((f) => f.id === id);
const custById = (id) => S.customers.find((c) => c.id === id);
const num = (v, d = 0) => (v === null || v === undefined || v === "" ? "–" : Number(v).toFixed(d));
const todayLocal = () => localDate(Date.now() / 1000);

// ---------- boot ----------
async function boot() {
  initMap();
  initTabs();
  try {
    const b = await api("GET", "/api/bootstrap");
    Object.assign(S, { settings: b.settings, customers: b.customers, fields: b.fields, sensitive: b.sensitive, pilots: b.pilots, aircraft: b.aircraft, jobs: b.jobs, mock: b.mock });
  } catch (e) { toast("Could not load data: " + e.message, 6000); return; }
  if (S.mock) showBanner("Demo weather: this server uses a synthetic forecast (SWATH_MOCK_WEATHER=1).");
  renderAll();
  fitToFields();
  loadNow();
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {});
}
function showBanner(t) { const b = $("banner"); b.textContent = t; b.classList.remove("hidden"); }
function renderAll() { drawFields(); drawSensitive(); renderFields(); renderJobs(); renderSettings(); renderPlanForm(); }

function initTabs() {
  document.querySelectorAll("#tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
}
function showTab(name) {
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("hidden", t.id !== "tab-" + name));
  if (name === "records") loadRecords();
  if (name === "plan") renderPlanForm();
  if (name !== "plan" && routeLayer) routeLayer.clearLayers();
  $("panel").scrollTop = 0;
}

// ---------- map ----------
function initMap() {
  map = L.map("map", { zoomControl: true }).setView([41.6, -93.6], 5);
  const sat = L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    { maxZoom: 19, attribution: "Imagery © Esri" });
  const labels = L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}", { maxZoom: 19 });
  const streets = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "© OpenStreetMap" });
  const hybrid = L.layerGroup([sat, labels]).addTo(map);
  L.control.layers({ Satellite: hybrid, Streets: streets }, null, { position: "topright" }).addTo(map);
  sensLayer = L.featureGroup().addTo(map);
  fieldLayer = L.featureGroup().addTo(map);
  routeLayer = L.featureGroup().addTo(map);
  previewLayer = L.featureGroup().addTo(map);
  map.on(L.Draw.Event.CREATED, (e) => onDrawn(e.layer));
  map.on("click", (e) => {
    if (!pickBase) return;
    pickBase = false; $("drawBar").classList.add("hidden");
    const f = $("crewForm"); f.querySelector("[name=base_lat]").value = e.latlng.lat.toFixed(5); f.querySelector("[name=base_lon]").value = e.latlng.lng.toFixed(5);
    toast("Base set. Save crew settings to keep it.");
  });
}
function statusOfField(id) {
  const r = S.now && S.now.fields.find((x) => x.field_id === id);
  return (r && r.status) || "none";
}
function drawFields() {
  fieldLayer.clearLayers();
  for (const f of S.fields) {
    if (!f.geom) continue;
    const st = statusOfField(f.id);
    const lyr = L.geoJSON(f.geom, { style: { color: STATUS_COLOR[st], weight: 2, fillColor: STATUS_COLOR[st], fillOpacity: 0.28 } });
    lyr.bindTooltip(esc(f.name), { permanent: S.fields.length <= 60, direction: "center", className: "field-label" });
    lyr.on("click", () => openField(f.id));
    lyr.addTo(fieldLayer);
  }
}
function drawSensitive() {
  sensLayer.clearLayers();
  for (const a of S.sensitive) {
    if (!a.geom) continue;
    L.geoJSON(a.geom, { style: { color: "#b98cff", weight: 2, dashArray: "6 4", fillColor: "#7b3fbf", fillOpacity: 0.25 } })
      .bindTooltip(`${esc(a.name || a.kind)} · buffer ${num(a.buffer_ft)} ft`).on("click", () => editSensitive(a)).addTo(sensLayer);
  }
}
function fitToFields() {
  const b = fieldLayer.getBounds();
  if (b.isValid()) map.fitBounds(b.pad(0.15));
}
function zoomTo(geom) { const b = L.geoJSON(geom).getBounds(); if (b.isValid()) map.fitBounds(b.pad(0.4)); }

// ---------- drawing ----------
function startDraw(kind) {
  cancelDraw();
  drawKind = kind;
  const color = kind === "field" ? "#ffe14d" : "#b98cff";
  drawHandler = new L.Draw.Polygon(map, { allowIntersection: false, showArea: true, metric: false, shapeOptions: { color, weight: 3 } });
  drawHandler.enable();
  $("drawMsg").textContent = kind === "field" ? "Tap the field corners, then Finish." : "Outline the sensitive area, then Finish.";
  $("drawBtns").classList.remove("hidden");
  $("drawBar").classList.remove("hidden");
  window.scrollTo(0, 0);
}
function finishDraw() {
  if (!drawHandler) return;
  if ((drawHandler._markers || []).length < 3) { toast("Tap at least 3 corners"); return; }
  drawHandler.completeShape();
}
function undoPoint() { if (drawHandler) drawHandler.deleteLastVertex(); }
function cancelDraw() {
  if (drawHandler) drawHandler.disable();
  drawHandler = null; drawKind = null; pickBase = false;
  $("drawBar").classList.add("hidden");
}
function onDrawn(layer) {
  const geom = layer.toGeoJSON().geometry;
  const kind = drawKind;
  cancelDraw();
  previewLayer.clearLayers(); layer.addTo(previewLayer);
  if (kind === "field") editField(null, geom); else editSensitive(null, geom);
}

// ---------- forms ----------
function formHtml(spec, v = {}) {
  return `<div class="form">${spec.map((s) => {
    const val = v[s.k] ?? s.default ?? "";
    const full = s.full || s.type === "textarea" ? " full" : "";
    const req = s.required ? " required" : "";
    const help = s.help ? `<span class="help">${esc(s.help)}</span>` : "";
    if (s.type === "checkbox") return `<label class="check${full}"><input type="checkbox" name="${s.k}" ${val ? "checked" : ""}> ${esc(s.label)}</label>`;
    if (s.type === "select") return `<label class="${full}">${esc(s.label)}<select name="${s.k}"${req}>${(s.options || []).map(([ov, ol]) =>
      `<option value="${esc(ov)}" ${String(ov) === String(val) ? "selected" : ""}>${esc(ol)}</option>`).join("")}</select>${help}</label>`;
    if (s.type === "textarea") return `<label class="full">${esc(s.label)}<textarea name="${s.k}">${esc(val)}</textarea>${help}</label>`;
    const extra = [s.step ? `step="${s.step}"` : "", s.min !== undefined ? `min="${s.min}"` : "", s.placeholder ? `placeholder="${esc(s.placeholder)}"` : "", s.list ? `list="${s.list}"` : ""].join(" ");
    return `<label class="${full}">${esc(s.label)}<input name="${s.k}" type="${s.type || "text"}" value="${esc(val)}" ${extra}${req}>${help}</label>`;
  }).join("")}</div>`;
}
function readForm(root, spec) {
  const o = {};
  for (const s of spec) {
    const el = root.querySelector(`[name="${s.k}"]`);
    if (!el) continue;
    if (s.type === "checkbox") o[s.k] = el.checked;
    else if (s.type === "number") o[s.k] = el.value === "" ? null : Number(el.value);
    else if (s.type === "select" && s.int) o[s.k] = el.value === "" ? null : parseInt(el.value, 10);
    else o[s.k] = el.value;
  }
  return o;
}
function openModal(html) { $("modalBody").innerHTML = html; $("modal").classList.remove("hidden"); }
function closeModal() { $("modal").classList.add("hidden"); $("modalBody").innerHTML = ""; previewLayer.clearLayers(); }
$("modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") { closeModal(); cancelDraw(); } });

function openForm({ title, spec, values, submit = "Save", onSubmit, onDelete, deleteLabel = "Delete", before = "", after = "", onRender }) {
  openModal(`<h2>${esc(title)}</h2>${before}<form id="mf">${formHtml(spec, values)}${after}<p class="err" id="mfErr"></p>
    <div class="form-actions">${onDelete ? `<button type="button" class="danger left" id="mfDel">${esc(deleteLabel)}</button>` : ""}
    <button type="button" onclick="closeModal()">Cancel</button><button class="primary" type="submit">${esc(submit)}</button></div></form>`);
  const f = $("mf");
  f.addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = f.querySelector("button[type=submit]"); btn.disabled = true;
    try { await onSubmit(readForm(f, spec), f); closeModal(); }
    catch (err) { $("mfErr").textContent = err.message; }
    finally { btn.disabled = false; }
  });
  if (onDelete) $("mfDel").addEventListener("click", async () => {
    if (!confirm(`${deleteLabel}?`)) return;
    try { await onDelete(); closeModal(); } catch (err) { $("mfErr").textContent = err.message; }
  });
  if (onRender) onRender(f);
}
async function reload(...tables) {
  for (const t of tables) S[t] = await api("GET", "/api/" + t);
  renderAll();
}

// ---------- NOW ----------
async function loadNow(force) {
  $("nowMeta").textContent = "Loading forecast…";
  try { S.now = await api("GET", "/api/now"); }
  catch (e) { $("nowMeta").textContent = "Forecast unavailable: " + e.message; return; }
  renderNow(); drawFields();
  if (force) toast("Updated");
}
function windTxt(h) {
  if (h.wind === undefined || h.wind === null) return "";
  const to = ((h.dir ?? 0) + 180) % 360;
  return `<span title="Wind blows toward ${compass(to)}"><span class="arrow" style="transform:rotate(${to}deg)">↑</span> ${num(h.wind, h.wind < 10 ? 1 : 0)} mph from ${esc(h.dir_txt || "")}</span>`;
}
function renderNow() {
  const list = $("nowList");
  if (!S.fields.length) { list.innerHTML = `<div class="card"><b>No fields yet.</b><p class="small muted">Go to <a href="#" onclick="showTab('fields');return false">Fields</a> to draw one on the map or import boundaries (KML, KMZ, GeoJSON, zipped shapefile).</p></div>`; $("nowMeta").textContent = ""; return; }
  const rows = (S.now && S.now.fields) || [];
  $("nowMeta").textContent = S.now ? `As of ${fmtTime(S.now.now)} · ${rows.filter((r) => r.status === "go").length} of ${rows.length} fields GO` : "";
  list.innerHTML = rows.map((r) => {
    const st = r.status || "none";
    const when = r.status === "go" ? (r.go_until ? `GO until ${fmtTime(r.go_until)}` : "GO for the rest of the forecast")
      : (r.next_go ? `Next GO ${localDate(r.next_go) === todayLocal() ? "" : fmtDay(r.next_go) + " "}${fmtTime(r.next_go)}` : "No GO hours in the forecast");
    const reasons = (r.reasons || []).slice(0, 4).map(([lvl, t]) => `<li class="${lvl}">${esc(t)}</li>`).join("");
    return `<div class="card click ${st}" onclick="openField(${r.field_id})">
      <div class="row between"><span class="title">${esc(r.name)}</span><span class="badge ${st}">${STATUS_TXT[st] || "–"}</span></div>
      <div class="wx">${windTxt(r)}<span>gust ${num(r.gust)}</span><span>${num(r.temp)}°F</span><span>${num(r.rh)}% RH</span><span>ΔT ${num(r.dt, 1)}°C</span></div>
      <div class="meta">${esc(when)}</div>${reasons ? `<ul class="reasons">${reasons}</ul>` : ""}</div>`;
  }).join("");
}

// ---------- FIELDS ----------
function customerOptions(withNew = true) {
  return [["", "— none —"], ...S.customers.map((c) => [c.id, c.name]), ...(withNew ? [["__new", "+ New customer…"]] : [])];
}
function renderFields() {
  const by = {};
  for (const f of S.fields) (by[f.customer_id || 0] = by[f.customer_id || 0] || []).push(f);
  const groups = Object.keys(by).sort((a, b) => (custById(+a)?.name || "~").localeCompare(custById(+b)?.name || "~"));
  $("fieldList").innerHTML = S.fields.length ? groups.map((cid) => {
    const fs = by[cid].sort((a, b) => (a.name || "").localeCompare(b.name || ""));
    const ac = fs.reduce((s, f) => s + (f.acres || 0), 0);
    return `<div class="muted small mt">${esc(custById(+cid)?.name || "No customer")} · ${num(ac)} ac</div>` + fs.map((f) =>
      `<div class="card click" onclick="openField(${f.id})"><div class="row between"><span class="title">${esc(f.name)}</span><span class="badge ${statusOfField(f.id)}">${STATUS_TXT[statusOfField(f.id)] || "–"}</span></div>
       <div class="meta">${num(f.acres, 1)} ac${f.crop ? " · " + esc(f.crop) : ""}</div></div>`).join("");
  }).join("") : `<p class="muted small">No fields yet. Draw one on the map or import a boundary file.</p>`;
  $("sensList").innerHTML = S.sensitive.map((a) => `<div class="card click" onclick='editSensitive(S.sensitive.find(x=>x.id===${a.id}))'>
    <span class="title">${esc(a.name || a.kind)}</span><div class="meta">${esc((SENS_KINDS.find((k) => k[0] === a.kind) || [0, a.kind])[1] || "")} · buffer ${num(a.buffer_ft)} ft</div></div>`).join("") || `<p class="muted small">None.</p>`;
  $("custList").innerHTML = S.customers.map((c) => `<div class="card click" onclick="editCustomer(${c.id})"><span class="title">${esc(c.name)}</span>
    <div class="meta">${esc(c.address || "")}${c.phone ? " · " + esc(c.phone) : ""}</div></div>`).join("") || `<p class="muted small">None.</p>`;
}
async function ensureCustomer(v, form) {
  if (v !== "__new") return v === "" ? null : parseInt(v, 10);
  const name = prompt("New customer name");
  if (!name) throw new Error("Pick a customer");
  const c = await api("POST", "/api/customers", { name });
  S.customers.push(c);
  return c.id;
}
const FIELD_SPEC = () => [
  { k: "name", label: "Field name", required: true, full: true },
  { k: "customer_id", label: "Customer", type: "select", options: customerOptions() },
  { k: "crop", label: "Crop", placeholder: "Corn, soybeans…" },
  { k: "notes", label: "Notes (gates, hazards, power lines)", type: "textarea" },
];
function editField(id, geom) {
  const f = id ? fieldById(id) : { name: "" };
  openForm({
    title: id ? "Edit field" : "New field", spec: FIELD_SPEC(), values: f,
    onSubmit: async (v) => {
      v.customer_id = await ensureCustomer(v.customer_id);
      if (geom) v.geom = geom;
      const saved = id ? await api("PUT", "/api/fields/" + id, v) : await api("POST", "/api/fields", v);
      toast(`${saved.name}: ${num(saved.acres, 1)} acres saved`);
      await reload("fields", "customers"); loadNow();
    },
    onDelete: id ? async () => { await api("DELETE", "/api/fields/" + id); await reload("fields", "jobs"); loadNow(); } : null,
  });
}
async function openField(id) {
  const f = fieldById(id);
  if (!f) return;
  if (f.geom) zoomTo(f.geom);
  const c = custById(f.customer_id);
  const jobs = S.jobs.filter((j) => j.field_id === id && j.status === "open");
  openModal(`<div class="row between"><h2>${esc(f.name)}</h2><span class="badge ${statusOfField(id)}">${STATUS_TXT[statusOfField(id)] || "–"}</span></div>
    <div class="meta">${num(f.acres, 1)} ac${f.crop ? " · " + esc(f.crop) : ""}${c ? " · " + esc(c.name) : ""}</div>
    ${f.notes ? `<p class="small">${esc(f.notes)}</p>` : ""}
    <h3 class="mt">Spray windows</h3><div id="fwStrip" class="muted small">Loading…</div><div id="fwInfo" class="hourinfo hidden"></div>
    <h3 class="mt">Open jobs</h3>${jobs.map((j) => `<div class="card"><b>${esc(j.product || "Job")}</b> <span class="meta">${esc(PRIORITY[j.priority] || "")}</span></div>`).join("") || `<p class="muted small">None.</p>`}
    <div class="form-actions"><button class="left" onclick="closeModal();editField(${id})">Edit</button>
      <button onclick="closeModal()">Close</button><button class="primary" onclick="closeModal();editJob(null, ${id})">+ Job here</button></div>`);
  try {
    const w = await api("GET", "/api/windows?field_ids=" + id);
    S.windows[id] = w[id];
    renderStrip($("fwStrip"), w[id] && w[id].hours, $("fwInfo"));
  } catch (e) { $("fwStrip").textContent = "Forecast unavailable: " + e.message; }
}
function renderStrip(el, hours, infoEl) {
  if (!el) return;
  if (!hours || !hours.length) { el.textContent = "No forecast."; return; }
  const days = {};
  for (const h of hours) (days[localDate(h.t)] = days[localDate(h.t)] || {})[localHour(h.t)] = h;
  let html = `<div class="strip"><span></span>${Array.from({ length: 24 }, (_, i) => `<span class="hr">${i % 3 === 0 ? (i % 12 || 12) + (i < 12 ? "a" : "p") : ""}</span>`).join("")}`;
  for (const [d, hs] of Object.entries(days)) {
    const any = Object.values(hs)[0];
    html += `<span class="lab">${esc(fmtDay(any.t).split(",")[0])}</span>`;
    for (let i = 0; i < 24; i++) {
      const h = hs[i];
      html += h ? `<span class="c ${h.status}" data-t="${h.t}" title="${esc(fmtTime(h.t))} ${STATUS_TXT[h.status]}"></span>` : `<span class="c none"></span>`;
    }
  }
  el.innerHTML = html + `</div><div class="small muted">Green GO · amber CAUTION · red NO-GO. Tap an hour for details.</div>`;
  el.classList.remove("muted", "small");
  el.querySelectorAll(".c[data-t]").forEach((cell) => cell.addEventListener("click", () => {
    el.querySelectorAll(".c.sel").forEach((x) => x.classList.remove("sel")); cell.classList.add("sel");
    const h = hours.find((x) => x.t === +cell.dataset.t);
    infoEl.classList.remove("hidden");
    infoEl.innerHTML = `<b>${esc(fmtDay(h.t))} ${esc(fmtTime(h.t))}</b> <span class="badge ${h.status}">${STATUS_TXT[h.status]}</span>
      <div class="wx">${windTxt(h)}<span>gust ${num(h.gust)}</span><span>${num(h.temp)}°F</span><span>${num(h.rh)}% RH</span><span>ΔT ${num(h.dt, 1)}°C</span><span>rain ${num(h.pop)}%</span></div>
      ${h.reasons.length ? `<ul class="reasons">${h.reasons.map(([l, t]) => `<li class="${l}">${esc(t)}</li>`).join("")}</ul>` : ""}`;
  }));
}
async function importFile(input) {
  const file = input.files[0];
  input.value = "";
  if (!file) return;
  const fd = new FormData(); fd.append("file", file);
  let res;
  try {
    const r = await fetch("/api/import", { method: "POST", body: fd });
    res = await r.json();
    if (!r.ok) throw new Error(res.error || r.statusText);
  } catch (e) { toast("Import failed: " + e.message, 6000); return; }
  const feats = res.features;
  previewLayer.clearLayers();
  feats.forEach((f) => L.geoJSON(f.geometry, { style: { color: "#ffe14d", weight: 2, fillOpacity: 0.15 } }).addTo(previewLayer));
  if (previewLayer.getBounds().isValid()) map.fitBounds(previewLayer.getBounds().pad(0.2));
  const spec = [{ k: "customer_id", label: "Customer for all", type: "select", options: customerOptions() }, { k: "crop", label: "Crop (optional)" }];
  openForm({
    title: `Import ${feats.length} field${feats.length === 1 ? "" : "s"}`, spec, values: {}, submit: "Import",
    before: `<p class="small muted">${esc(file.name)}</p><table class="simple"><tr><th></th><th>Name</th><th>Acres</th></tr>${feats.map((f, i) =>
      `<tr><td><input type="checkbox" class="impSel" data-i="${i}" checked></td><td><input class="impName" data-i="${i}" value="${esc(f.name)}"></td><td>${num(f.acres, 1)}</td></tr>`).join("")}</table><br>`,
    onSubmit: async (v) => {
      const cid = await ensureCustomer(v.customer_id);
      const chosen = [...document.querySelectorAll(".impSel")].filter((c) => c.checked).map((c) => +c.dataset.i);
      const names = Object.fromEntries([...document.querySelectorAll(".impName")].map((n) => [+n.dataset.i, n.value]));
      if (!chosen.length) throw new Error("Nothing selected");
      await api("POST", "/api/fields/bulk", { customer_id: cid, crop: v.crop || null, fields: chosen.map((i) => ({ name: names[i] || feats[i].name, geometry: feats[i].geometry })) });
      toast(`Imported ${chosen.length} field${chosen.length === 1 ? "" : "s"}`);
      await reload("fields", "customers"); fitToFields(); loadNow();
    },
  });
}
function editSensitive(a, geom) {
  const spec = [
    { k: "name", label: "Name", required: true, placeholder: "Smith apiary" },
    { k: "kind", label: "Kind", type: "select", options: SENS_KINDS },
    { k: "buffer_ft", label: "Buffer (ft)", type: "number", step: 50, min: 0, help: "Use the label's buffer or your state's rule." },
    { k: "notes", label: "Notes", type: "textarea" },
  ];
  openForm({
    title: a ? "Sensitive area" : "New sensitive area", spec, values: a || { buffer_ft: 500, kind: "apiary" },
    onSubmit: async (v) => {
      if (geom) v.geom = geom;
      if (a) await api("PUT", "/api/sensitive/" + a.id, v); else await api("POST", "/api/sensitive", v);
      await reload("sensitive"); loadNow();
    },
    onDelete: a ? async () => { await api("DELETE", "/api/sensitive/" + a.id); await reload("sensitive"); loadNow(); } : null,
  });
}
function editCustomer(id) {
  const c = id ? custById(id) : {};
  const spec = [{ k: "name", label: "Name", required: true, full: true }, { k: "address", label: "Address (Part 137 record)", full: true },
    { k: "phone", label: "Phone", type: "tel" }, { k: "email", label: "Email", type: "email" }, { k: "notes", label: "Notes", type: "textarea" }];
  openForm({
    title: id ? "Customer" : "New customer", spec, values: c,
    onSubmit: async (v) => { if (id) await api("PUT", "/api/customers/" + id, v); else await api("POST", "/api/customers", v); await reload("customers"); },
    onDelete: id ? async () => { await api("DELETE", "/api/customers/" + id); await reload("customers"); } : null,
  });
}

// ---------- JOBS ----------
function fieldOptions() {
  return S.fields.slice().sort((a, b) => (a.name || "").localeCompare(b.name || ""))
    .map((f) => [f.id, `${f.name}${custById(f.customer_id) ? " — " + custById(f.customer_id).name : ""} (${num(f.acres)} ac)`]);
}
function renderJobs() {
  const open = S.jobs.filter((j) => j.status === "open").sort((a, b) => (b.priority || 1) - (a.priority || 1) || (a.due_date || "9").localeCompare(b.due_date || "9"));
  const ac = open.reduce((s, j) => s + (fieldById(j.field_id)?.acres || 0), 0);
  $("jobList").innerHTML = open.length ? `<p class="muted small">${open.length} open · ${num(ac)} acres</p>` + open.map((j) => {
    const f = fieldById(j.field_id) || {};
    return `<div class="card"><div class="row between"><span class="title">${esc(f.name || "?")}</span><span class="meta">${esc(PRIORITY[j.priority] || "")}${j.due_date ? " · due " + esc(j.due_date) : ""}</span></div>
      <div class="meta">${esc(custById(f.customer_id)?.name || "")} · ${num(f.acres, 1)} ac · ${esc(j.product || "product?")} ${j.rate ? num(j.rate, 2) + " " + esc(j.rate_unit || "") : ""}</div>
      <div class="form-actions"><button class="left ghost" onclick="openField(${f.id})">Field</button><button onclick="editJob(${j.id})">Edit</button><button class="primary" onclick="completeJob(${j.id})">Complete</button></div></div>`;
  }).join("") : `<p class="muted small">No open jobs. Add one, then plan the day.</p>`;
}
const JOB_SPEC = () => [
  { k: "field_id", label: "Field", type: "select", options: fieldOptions(), int: true, full: true, required: true },
  { k: "product", label: "Product", placeholder: "Trade name" }, { k: "epa_reg", label: "EPA reg. no." },
  { k: "rate", label: "Rate", type: "number", step: "any", min: 0 }, { k: "rate_unit", label: "Rate unit", type: "select", options: RATE_UNITS.map((u) => [u, u]) },
  { k: "carrier_gpa", label: "Carrier (gal/ac)", type: "number", step: "any", min: 0 }, { k: "target", label: "Target pest / purpose" },
  { k: "priority", label: "Priority", type: "select", options: [[1, "Normal"], [2, "High"], [3, "Urgent"]], int: true },
  { k: "due_date", label: "Due by", type: "date" }, { k: "notes", label: "Notes", type: "textarea" },
];
function editJob(id, fieldId) {
  if (!S.fields.length) { toast("Add a field first"); showTab("fields"); return; }
  const j = id ? S.jobs.find((x) => x.id === id) : { field_id: fieldId || S.fields[0].id, priority: 1, rate_unit: "fl oz/ac", carrier_gpa: 2 };
  openForm({
    title: id ? "Edit job" : "New job", spec: JOB_SPEC(), values: j,
    onSubmit: async (v) => { if (id) await api("PUT", "/api/jobs/" + id, v); else await api("POST", "/api/jobs", v); await reload("jobs"); toast("Job saved"); },
    onDelete: id ? async () => { await api("PUT", "/api/jobs/" + id, { status: "cancelled" }); await reload("jobs"); } : null, deleteLabel: "Cancel job",
  });
}
const RECORD_SPEC = () => [
  { k: "applied_date", label: "Date", type: "date", required: true }, { k: "acres_treated", label: "Acres treated", type: "number", step: "any", min: 0 },
  { k: "start_time", label: "Start", type: "time" }, { k: "end_time", label: "End", type: "time" },
  { k: "pilot_id", label: "Pilot", type: "select", options: [["", "— pick —"], ...S.pilots.filter((p) => p.active !== 0).map((p) => [p.id, p.name])], int: true, required: true },
  { k: "aircraft_id", label: "Aircraft", type: "select", options: [["", "— none —"], ...S.aircraft.filter((a) => a.active !== 0).map((a) => [a.id, `${a.name}${a.registration ? " " + a.registration : ""}`])], int: true },
  { k: "product", label: "Product" }, { k: "epa_reg", label: "EPA reg. no." },
  { k: "rate", label: "Rate", type: "number", step: "any" }, { k: "rate_unit", label: "Rate unit", type: "select", options: RATE_UNITS.map((u) => [u, u]) },
  { k: "total_amount", label: "Total product applied", type: "number", step: "any", help: "Blank = rate × acres" }, { k: "amount_unit", label: "Total unit", placeholder: "fl oz, gal…" },
  { k: "wx_wind", label: "Wind (mph)", type: "number", step: "any" }, { k: "wx_dir", label: "Wind from (°)", type: "number", step: 1, min: 0 },
  { k: "wx_gust", label: "Gust (mph)", type: "number", step: "any" }, { k: "wx_temp", label: "Temp (°F)", type: "number", step: "any" },
  { k: "wx_rh", label: "Humidity (%)", type: "number", step: "any" }, { k: "notes", label: "Notes", type: "textarea" },
];
function completeJob(id, existing) {
  const j = S.jobs.find((x) => x.id === id) || existing;
  const f = fieldById(j.field_id) || {};
  if (!S.pilots.length) { toast("Add a pilot in Settings first (Part 137 record)", 4000); showTab("settings"); return; }
  const now = new Date();
  const v = existing ? { ...existing } : {
    ...j, applied_date: todayLocal(), acres_treated: f.acres ? +f.acres.toFixed(1) : null,
    start_time: now.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: tz() }),
    pilot_id: S.pilots.length === 1 ? S.pilots[0].id : "", aircraft_id: S.aircraft.length === 1 ? S.aircraft[0].id : "",
  };
  openForm({
    title: existing ? `Record: ${f.name || ""}` : `Complete: ${f.name || ""}`, spec: RECORD_SPEC(), values: v, submit: existing ? "Save record" : "Save record",
    before: `<p class="small muted">Weather fills in from the forecast for the start hour. Overwrite it with what you measured on site.</p>`,
    onSubmit: async (vals) => {
      if (existing) await api("PUT", "/api/jobs/" + id, vals); else await api("POST", `/api/jobs/${id}/complete`, vals);
      await reload("jobs"); toast("Record saved"); if (!$("tab-records").classList.contains("hidden")) loadRecords();
    },
    onRender: (form) => {
      const fill = async () => {
        if (existing) return;
        const d = form.querySelector("[name=applied_date]").value, t = form.querySelector("[name=start_time]").value;
        try {
          const wx = await api("GET", `/api/jobs/${id}/wx?date=${encodeURIComponent(d)}&time=${encodeURIComponent(t)}`);
          for (const k of ["wx_wind", "wx_dir", "wx_gust", "wx_temp", "wx_rh"]) if (wx[k] !== undefined) form.querySelector(`[name=${k}]`).value = wx[k];
        } catch (e) { /* leave blank */ }
      };
      form.querySelector("[name=applied_date]").addEventListener("change", fill);
      form.querySelector("[name=start_time]").addEventListener("change", fill);
      fill();
    },
  });
}

// ---------- PLAN ----------
function renderPlanForm() {
  if (!S.settings) return;
  const sel = $("planDate");
  const keep = sel.value;
  const base = Date.now() / 1000;
  sel.innerHTML = [0, 1, 2].map((d) => { const ts = base + d * 86400; return `<option value="${localDate(ts)}">${d === 0 ? "Today" : d === 1 ? "Tomorrow" : fmtDay(ts)}</option>`; }).join("");
  if (keep) sel.value = keep;
  const c = S.settings.crew;
  if (!$("planStart").value) { $("planStart").value = c.start; $("planEnd").value = c.end; $("planRate").value = c.acres_per_hour; $("planPause").checked = !!c.allow_pause; $("planCaution").checked = !!c.allow_caution; }
  const open = S.jobs.filter((j) => j.status === "open");
  const prev = new Set([...document.querySelectorAll(".planJob")].filter((x) => !x.checked).map((x) => +x.value));
  $("planJobCount").textContent = open.length;
  $("planJobs").innerHTML = `<div class="joblist">${open.map((j) => { const f = fieldById(j.field_id) || {};
    return `<label><input type="checkbox" class="planJob" value="${j.id}" ${prev.has(j.id) ? "" : "checked"}> ${esc(f.name)} · ${num(f.acres)} ac · ${esc(PRIORITY[j.priority] || "")}</label>`; }).join("")}</div>`;
}
async function runPlan() {
  const ids = [...document.querySelectorAll(".planJob")].filter((x) => x.checked).map((x) => +x.value);
  if (!ids.length) { toast("No open jobs selected"); return; }
  const crew = { start: $("planStart").value, end: $("planEnd").value, acres_per_hour: +$("planRate").value, allow_pause: $("planPause").checked, allow_caution: $("planCaution").checked };
  const btn = $("planBtn"); btn.disabled = true; btn.textContent = "Planning…";
  try {
    const p = await api("POST", "/api/plan", { date: $("planDate").value, job_ids: ids, crew });
    api("PUT", "/api/settings", { crew: { ...S.settings.crew, ...crew } }).then((s) => { S.settings = s; }).catch(() => {});
    const fids = [...new Set(ids.map((i) => S.jobs.find((j) => j.id === i)?.field_id).filter(Boolean))];
    const w = await api("GET", "/api/windows?field_ids=" + fids.join(","));
    Object.assign(S.windows, w);
    renderPlan(p);
  } catch (e) { $("planOut").innerHTML = `<p class="err">${esc(e.message)}</p>`; }
  finally { btn.disabled = false; btn.textContent = "Plan day"; }
}
function renderPlan(p) {
  routeLayer.clearLayers();
  const stops = p.stops;
  const spray = stops.reduce((s, x) => s + (x.finish - x.start), 0);
  let html = `<div class="card"><b>${num(p.acres)} of ${num(p.of_acres)} acres</b> · ${stops.length} job${stops.length === 1 ? "" : "s"}
    <div class="meta">${stops.length ? `Leave base by ${hhmm12(stops[0].start - p.crew.setup_min - stops[0].travel)} · spraying ${dur(spray)} · driving ${dur(p.travel_min)} (incl. ${dur(p.return_min)} back) · done ${hhmm12(stops[stops.length - 1].finish)}` : "Nothing fits the weather and crew hours on this day."}</div></div>`;
  html += timelineHtml(p);
  html += stops.map((s, i) => `<div class="card"><div class="row"><span class="stop-num">${i + 1}</span><span class="title">${esc(s.name)}</span></div>
    <div class="meta">${num(s.acres, 1)} ac · drive ${dur(s.travel)} · arrive ${hhmm12(s.arrive)}</div>
    <div>Spray <b>${hhmm12(s.start)}–${hhmm12(s.finish)}</b>${s.wait > 0.5 ? ` · wait ${dur(s.wait)} for the window` : ""}${s.paused > 0 ? ` · paused ${dur(s.paused)}` : ""}</div></div>`).join("");
  if (p.unscheduled.length) html += `<h3 class="mt">Not today</h3>` + p.unscheduled.map((u) => `<div class="card nogo"><span class="title">${esc(u.name)}</span><div class="meta">${num(u.acres, 1)} ac · ${esc(u.reason)}</div></div>`).join("");
  html += `<p class="fine">Plan uses ${p.crew.allow_caution ? "GO and CAUTION" : "GO"} hours only, ${p.crew.acres_per_hour} ac/h, ${p.crew.setup_min} min setup per field, ${p.crew.road_mph} mph roads ×${p.crew.detour}. Change defaults in Settings.</p>`;
  $("planOut").innerHTML = html;
  // map
  const pts = [[p.base.lat, p.base.lon], ...stops.map((s) => [s.lat, s.lon]), [p.base.lat, p.base.lon]];
  if (stops.length) {
    L.polyline(pts, { color: "#ffffff", weight: 6, opacity: 0.9 }).addTo(routeLayer);
    L.polyline(pts, { color: "#1f5f3a", weight: 3, dashArray: "8 6" }).addTo(routeLayer);
    L.marker([p.base.lat, p.base.lon], { icon: L.divIcon({ className: "base-icon", html: "<div>⌂</div>", iconSize: [22, 22] }) }).bindTooltip("Base").addTo(routeLayer);
    stops.forEach((s, i) => L.marker([s.lat, s.lon], { icon: L.divIcon({ className: "num-icon", html: `<div>${i + 1}</div>`, iconSize: [26, 26] }) })
      .bindTooltip(`${i + 1}. ${esc(s.name)} ${hhmm12(s.start)}–${hhmm12(s.finish)}`).addTo(routeLayer));
    map.fitBounds(routeLayer.getBounds().pad(0.2));
  }
}
function timelineHtml(p) {
  const rows = [...p.stops.map((s) => ({ name: s.name, job: s.job_id, fid: s.field_id, s })), ...p.unscheduled.map((u) => ({ name: u.name, job: u.job_id }))];
  if (!rows.length) return "";
  const startH = Math.floor(Math.min(toMin(p.crew.start), ...p.stops.map((s) => s.arrive)) / 60);
  const endH = Math.ceil(Math.max(toMin(p.crew.end), ...p.stops.map((s) => s.finish)) / 60);
  const span = (endH - startH) * 60;
  const pct = (m) => ((m - startH * 60) / span * 100).toFixed(2) + "%";
  const dayStatus = (fid) => {
    const w = S.windows[fid]; const out = {};
    if (w) for (const h of w.hours) if (localDate(h.t) === p.date) out[localHour(h.t)] = h.status;
    return out;
  };
  let html = `<div class="timeline">`;
  for (const r of rows) {
    const fid = r.fid || S.jobs.find((j) => j.id === r.job)?.field_id;
    const st = dayStatus(fid);
    const cells = Array.from({ length: endH - startH }, (_, i) => `<span class="c ${st[startH + i] || "none"}"></span>`).join("");
    const bar = r.s ? `<span class="tl-wait" style="left:${pct(r.s.ready)};width:calc(${pct(r.s.start)} - ${pct(r.s.ready)})"></span><span class="tl-bar" style="left:${pct(r.s.start)};width:calc(${pct(r.s.finish)} - ${pct(r.s.start)})"></span>` : "";
    html += `<div class="tl-row"><span class="tl-name" title="${esc(r.name)}">${esc(r.name)}</span><div class="tl-track">${cells}${bar}</div></div>`;
  }
  const ticks = []; for (let h = startH; h <= endH; h += Math.max(1, Math.round((endH - startH) / 6))) ticks.push(`<span>${(h % 12) || 12}${h < 12 ? "a" : "p"}</span>`);
  return html + `<div class="tl-axis"><span></span><div class="ticks">${ticks.join("")}</div></div><div class="small muted">Dark bar = planned spraying; dashes = waiting for the window.</div></div>`;
}
const toMin = (v) => (typeof v === "number" ? v : (([h, m]) => h * 60 + m)(v.split(":").map(Number)));

// ---------- RECORDS ----------
async function loadRecords() {
  if (!$("recFrom").value) { $("recFrom").value = todayLocal().slice(0, 4) + "-01-01"; $("recTo").value = todayLocal(); }
  const q = `from=${$("recFrom").value}&to=${$("recTo").value}`;
  try {
    const rows = await api("GET", "/api/records?" + q);
    $("recList").innerHTML = rows.length ? `<p class="muted small">${rows.length} records · ${num(rows.reduce((s, r) => s + (r.acres_treated || 0), 0))} acres</p>
      <table class="simple"><tr><th>Date</th><th>Field</th><th>Product</th><th>Ac</th><th>Pilot</th></tr>${rows.map((r) =>
      `<tr class="click" onclick="editRecord(${r.id})" style="cursor:pointer"><td>${esc(r.applied_date)}<br><span class="muted">${esc(r.start_time || "")}</span></td><td>${esc(r.field)}<br><span class="muted">${esc(r.customer || "")}</span></td>
       <td>${esc(r.product || "")}</td><td>${num(r.acres_treated, 1)}</td><td>${esc(r.pilot || "")}</td></tr>`).join("")}</table>` : `<p class="muted small">No records in this range.</p>`;
  } catch (e) { $("recList").innerHTML = `<p class="err">${esc(e.message)}</p>`; }
}
async function editRecord(id) { const r = await api("GET", "/api/jobs/" + id); completeJob(id, r); }
function exportRecords(kind) { window.open(`/api/records.${kind}?from=${$("recFrom").value}&to=${$("recTo").value}`, "_blank"); }

// ---------- SETTINGS ----------
const OP_SPEC = [{ k: "name", label: "Business name", full: true }, { k: "address", label: "Home base address", full: true }, { k: "phone", label: "Phone" },
  { k: "cert_no", label: "Part 137 certificate no." }, { k: "exemption_no", label: "44807 exemption no." }];
const CREW_SPEC = [
  { k: "base_lat", label: "Base latitude", type: "number", step: "any" }, { k: "base_lon", label: "Base longitude", type: "number", step: "any" },
  { k: "start", label: "Day starts", type: "time" }, { k: "end", label: "Spraying ends by", type: "time" },
  { k: "acres_per_hour", label: "Acres per hour (whole crew)", type: "number", step: 1, min: 1, help: "Including battery swaps and refills" },
  { k: "setup_min", label: "Setup per field (min)", type: "number", step: 1, min: 0 },
  { k: "road_mph", label: "Average road speed (mph)", type: "number", step: 1, min: 5 }, { k: "detour", label: "Road detour factor", type: "number", step: 0.05, min: 1, help: "Road miles ÷ straight-line miles" },
  { k: "max_pause_min", label: "Longest pause inside one job (min)", type: "number", step: 15, min: 0, help: "Longer gaps: the crew moves on" },
  { k: "allow_pause", label: "Pause a job through short no-go spells", type: "checkbox" }, { k: "allow_caution", label: "Plan in CAUTION hours", type: "checkbox" },
];
const LIM_SPEC = [
  { k: "wind_min", label: "Wind min (mph)", type: "number", step: 0.5 }, { k: "wind_max", label: "Wind max (mph)", type: "number", step: 0.5 },
  { k: "gust_max", label: "Gust max (mph)", type: "number", step: 0.5 }, { k: "wind_caution_margin", label: "Caution within (mph of max)", type: "number", step: 0.5 },
  { k: "wind_height", label: "Compare wind at", type: "select", options: [["10m", "10 m (forecast standard)"], ["release", "Release height (estimated)"]] },
  { k: "release_height_ft", label: "Release height (ft)", type: "number", step: 1 },
  { k: "temp_max", label: "Temp max (°F)", type: "number", step: 1 }, { k: "temp_caution", label: "Temp caution (°F)", type: "number", step: 1 },
  { k: "rh_min", label: "Humidity min (%)", type: "number", step: 1 }, { k: "rh_caution", label: "Humidity caution (%)", type: "number", step: 1 },
  { k: "dt_low", label: "Delta T low (°C)", type: "number", step: 0.5 }, { k: "dt_high", label: "Delta T high (°C)", type: "number", step: 0.5 },
  { k: "dt_nogo", label: "Delta T no-go (°C)", type: "number", step: 0.5 }, { k: "inv_wind", label: "Inversion check below (mph)", type: "number", step: 0.5 },
  { k: "inv_nogo", label: "Inversion no-go (°F, 80 m − 2 m)", type: "number", step: 0.1 }, { k: "inv_caution", label: "Inversion caution (°F)", type: "number", step: 0.1 },
  { k: "pop_nogo", label: "Rain chance no-go (%)", type: "number", step: 5 }, { k: "pop_caution", label: "Rain chance caution (%)", type: "number", step: 5 },
  { k: "rain_in", label: "Rain amount that counts (in)", type: "number", step: 0.01 }, { k: "rainfast_h", label: "Rain-fast period (h)", type: "number", step: 1, min: 0 },
  { k: "sens_nogo_deg", label: "Toward sensitive area: no-go within ±°", type: "number", step: 5 }, { k: "sens_caution_deg", label: "…caution within ±°", type: "number", step: 5 },
  { k: "daylight_only", label: "Daylight only", type: "checkbox" },
];
function renderSettings() {
  if (!S.settings) return;
  const sec = (id, spec, vals, key) => {
    $(id).innerHTML = formHtml(spec, vals) + `<div class="form-actions">${key === "crew" ? `<button type="button" class="left" onclick="useMyLocation()">📍 My location</button><button type="button" onclick="pickBaseOnMap()">Pick on map</button>` : ""}
      ${key === "limits" ? `<button type="button" class="left ghost" onclick="resetLimits()">Reset to defaults</button>` : ""}<button class="primary" onclick="saveSettings('${key}', '${id}')">Save</button></div>`;
  };
  sec("opForm", OP_SPEC, S.settings.operator, "operator");
  sec("crewForm", CREW_SPEC, S.settings.crew, "crew");
  sec("limForm", LIM_SPEC, S.settings.limits, "limits");
  $("pilotList").innerHTML = S.pilots.map((p) => `<div class="card click" onclick="editPilot(${p.id})"><span class="title">${esc(p.name)}</span>${p.active === 0 ? ' <span class="muted">(inactive)</span>' : ""}
    <div class="meta">Cert. ${esc(p.cert_no || "–")} · qualified ${esc(p.qual_date || "–")}</div></div>`).join("") || `<p class="muted small">Add each pilot: records need their certificate number and qualification date.</p>`;
  $("acList").innerHTML = S.aircraft.map((a) => `<div class="card click" onclick="editAircraft(${a.id})"><span class="title">${esc(a.name)}</span>${a.active === 0 ? ' <span class="muted">(inactive)</span>' : ""}
    <div class="meta">${esc(a.make_model || "")} ${esc(a.registration || "")} ${a.serial ? "· s/n " + esc(a.serial) : ""}</div></div>`).join("") || `<p class="muted small">None.</p>`;
}
async function saveSettings(key, id) {
  const spec = { operator: OP_SPEC, crew: CREW_SPEC, limits: LIM_SPEC }[key];
  try {
    S.settings = await api("PUT", "/api/settings", { [key]: readForm($(id), spec) });
    toast("Saved"); renderSettings();
    if (key === "limits") loadNow();
  } catch (e) { toast("Not saved: " + e.message, 5000); }
}
async function resetLimits() {
  if (!confirm("Reset all spray limits to the defaults?")) return;
  S.settings = await api("PUT", "/api/settings", { limits: {} }); renderSettings(); loadNow();
}
function useMyLocation() {
  if (!navigator.geolocation) { toast("Location not available"); return; }
  navigator.geolocation.getCurrentPosition((pos) => {
    const f = $("crewForm"); f.querySelector("[name=base_lat]").value = pos.coords.latitude.toFixed(5); f.querySelector("[name=base_lon]").value = pos.coords.longitude.toFixed(5);
    toast("Base set to your location. Save to keep it.");
  }, (e) => toast("Location failed: " + e.message));
}
function pickBaseOnMap() { cancelDraw(); pickBase = true; $("drawMsg").textContent = "Tap the map where the crew starts."; $("drawBtns").classList.add("hidden"); $("drawBar").classList.remove("hidden"); window.scrollTo(0, 0); }
function editPilot(id) {
  const p = id ? S.pilots.find((x) => x.id === id) : { active: 1 };
  const spec = [{ k: "name", label: "Name", required: true, full: true }, { k: "address", label: "Address", full: true },
    { k: "cert_no", label: "Remote pilot certificate no." }, { k: "qual_date", label: "Part 137 qualification date", type: "date" }, { k: "active", label: "Active", type: "checkbox" }];
  openForm({
    title: id ? "Pilot" : "New pilot", spec, values: p,
    onSubmit: async (v) => { v.active = v.active ? 1 : 0; if (id) await api("PUT", "/api/pilots/" + id, v); else await api("POST", "/api/pilots", v); await reload("pilots"); },
  });
}
function editAircraft(id) {
  const a = id ? S.aircraft.find((x) => x.id === id) : { active: 1 };
  const spec = [{ k: "name", label: "Name", required: true, placeholder: "T50 #1" }, { k: "make_model", label: "Make / model", placeholder: "DJI Agras T50" },
    { k: "registration", label: "Registration (N-number)" }, { k: "serial", label: "Serial no." }, { k: "active", label: "Active", type: "checkbox" }];
  openForm({
    title: id ? "Aircraft" : "New aircraft", spec, values: a,
    onSubmit: async (v) => { v.active = v.active ? 1 : 0; if (id) await api("PUT", "/api/aircraft/" + id, v); else await api("POST", "/api/aircraft", v); await reload("aircraft"); },
  });
}

boot();
