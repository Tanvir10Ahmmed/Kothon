"use strict";
/* Kothon front-end: vanilla JS, no build step. */

const $ = (sel) => document.querySelector(sel);
const S = {
  cfg: null, jobId: null, job: null, segs: [], nextId: 1,
  localUrl: null, pollTimer: null, saveTimer: null, saveChain: Promise.resolve(), dirty: false,
  activeIdx: -1, frameUrl: null, busyPreview: false,
};
const STORE_JOB = "kothon_job", STORE_STYLE = "kothon_style";

/* ---------- small helpers ---------- */
function lsGet(k) { try { return localStorage.getItem(k); } catch (_) { return null; } }
function lsSet(k, v) { try { if (v === null) localStorage.removeItem(k); else localStorage.setItem(k, v); } catch (_) { /* private mode */ } }
function errText(data, status) {
  if (data && typeof data.detail === "string") return data.detail;
  if (data && Array.isArray(data.detail)) return "Input thik na. Value gulo check koro.";
  return "Server error (" + status + ")";
}
async function api(url, opts) {
  let r;
  try { r = await fetch(url, opts); } catch (_) { throw new Error("Server er sathe connection nei. Terminal e app cholche to?"); }
  let data = null;
  try { data = await r.json(); } catch (_) { /* not json */ }
  if (!r.ok) { const e = new Error(errText(data, r.status)); e.status = r.status; throw e; }
  return data;
}
const jsonOpts = (method, body) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
function showError(msg) { $("#error-text").textContent = msg; $("#error").hidden = false; window.scrollTo({ top: 0, behavior: "smooth" }); }
function clearError() { $("#error").hidden = true; }
function show(view) {
  for (const v of ["upload", "progress", "editor"]) $("#view-" + v).hidden = v !== view;
  $("#btn-new").hidden = view === "upload";
}
function fmtTime(t) {
  t = Math.max(0, Number(t) || 0);
  const m = Math.floor(t / 60), s = t - m * 60;
  return m + ":" + (s < 10 ? "0" : "") + s.toFixed(1);
}

/* ---------- init ---------- */
async function init() {
  try {
    S.cfg = await api("/api/config");
  } catch (e) { showError(e.message); return; }
  $("#ver").textContent = S.cfg.version;
  $("#limits").textContent = `Maximum ${S.cfg.max_seconds} সেকেন্ড · ${S.cfg.max_upload_mb} MB · ${S.cfg.formats.join(" ")}`;
  const sel = $("#model");
  for (const m of S.cfg.models) { const o = document.createElement("option"); o.value = m.key; o.textContent = m.label; sel.appendChild(o); }
  sel.value = S.cfg.default_model;
  const note = () => { const m = S.cfg.models.find((x) => x.key === sel.value); $("#model-note").textContent = m ? m.note : ""; };
  sel.addEventListener("change", note); note();
  loadStyle(); bindUpload(); bindEditor();

  const saved = lsGet(STORE_JOB);
  if (saved) {
    try {
      const job = await api("/api/jobs/" + saved);
      S.jobId = saved; S.job = job;
      await onJob(job, true);
      return;
    } catch (_) { lsSet(STORE_JOB, null); }
  }
  show("upload");
}

/* ---------- upload ---------- */
function bindUpload() {
  const drop = $("#drop"), input = $("#file");
  drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } });
  input.addEventListener("change", () => { if (input.files[0]) upload(input.files[0]); input.value = ""; });
  for (const ev of ["dragenter", "dragover"]) drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); });
  for (const ev of ["dragleave", "drop"]) drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); });
  drop.addEventListener("drop", (e) => { const f = e.dataTransfer.files[0]; if (f) upload(f); });
}

function upload(file) {
  clearError();
  const ext = "." + (file.name.split(".").pop() || "").toLowerCase();
  if (!S.cfg.formats.includes(ext)) { showError("Ei format support kore na. Use koro: " + S.cfg.formats.join(", ")); return; }
  if (file.size > S.cfg.max_upload_mb * 1024 * 1024) { showError(`File onek boro. Maximum ${S.cfg.max_upload_mb} MB.`); return; }
  if (S.localUrl) { URL.revokeObjectURL(S.localUrl); S.localUrl = null; }
  show("progress");
  $("#progress-title").textContent = "ভিডিও আপলোড হচ্ছে…";
  $("#progress-msg").textContent = file.name;
  $("#btn-retry").hidden = true;
  setBar($("#progress-bar"), 0);

  const fd = new FormData();
  fd.append("file", file);
  fd.append("model", $("#model").value);
  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/jobs");
  xhr.upload.onprogress = (e) => { if (e.lengthComputable) setBar($("#progress-bar"), (100 * e.loaded) / e.total); };
  xhr.onerror = () => { show("upload"); showError("Upload fail hoyeche. Server cholche to?"); };
  xhr.onload = () => {
    let data = null; try { data = JSON.parse(xhr.responseText); } catch (_) { /* ignore */ }
    if (xhr.status !== 201 || !data) { show("upload"); showError(errText(data, xhr.status)); return; }
    S.jobId = data.id; S.job = data; S.localUrl = URL.createObjectURL(file);
    lsSet(STORE_JOB, S.jobId);
    onJob(data, false);
  };
  xhr.send(fd);
}

/* ---------- job state machine ---------- */
const STAGE_TEXT = {
  queued: "লাইনে আছে…", extracting: "অডিও আলাদা করা হচ্ছে…",
  loading_model: "Model load হচ্ছে (প্রথমবার ডাউনলোড হতে কয়েক মিনিট লাগতে পারে)…", transcribing: "বাংলা কথা লেখা হচ্ছে…",
};
function setBar(el, pct, indeterminate) {
  el.classList.toggle("indeterminate", !!indeterminate);
  el.style.width = Math.max(0, Math.min(100, pct)) + "%";
}
async function onJob(job, resumed) {
  S.job = job;
  if (job.stage === "failed") {
    stopPoll(); show("progress");
    $("#progress-title").textContent = "দুঃখিত, কাজটি হয়নি";
    $("#progress-msg").textContent = job.error || "Unknown error";
    setBar($("#progress-bar"), 0); $("#btn-retry").hidden = false;
    return;
  }
  if (job.stage !== "ready") {
    show("progress");
    $("#progress-title").textContent = STAGE_TEXT[job.stage] || "প্রসেস হচ্ছে…";
    $("#progress-msg").textContent = job.message || "";
    $("#btn-retry").hidden = true;
    setBar($("#progress-bar"), job.progress || 0, job.stage !== "transcribing");
    startPoll();
    return;
  }
  // ready: (re)load the editor once
  if (!$("#view-editor").dataset.loaded || $("#view-editor").dataset.job !== job.id) {
    try { await loadEditor(job); } catch (e) { showError(e.message); show("upload"); return; }
  }
  show("editor");
  updateRenderUi(job);
  if (job.render && (job.render.state === "queued" || job.render.state === "running")) startPoll(); else stopPoll();
}

function startPoll() {
  if (S.pollTimer) return;
  const tick = async () => {
    try {
      const job = await api("/api/jobs/" + S.jobId);
      S.pollTimer = null;
      await onJob(job, false);
      if (!S.pollTimer && (job.stage !== "ready" ? job.stage !== "failed" : job.render.state === "queued" || job.render.state === "running")) {
        S.pollTimer = setTimeout(tick, 1000);
      }
    } catch (e) {
      S.pollTimer = null;
      if (e.status === 404) { lsSet(STORE_JOB, null); show("upload"); showError(e.message); return; }
      S.pollTimer = setTimeout(tick, 2500); // temporary network hiccup: keep trying
    }
  };
  S.pollTimer = setTimeout(tick, 600);
}
function stopPoll() { if (S.pollTimer) { clearTimeout(S.pollTimer); S.pollTimer = null; } }

/* ---------- editor ---------- */
async function loadEditor(job) {
  const t = await api("/api/jobs/" + job.id + "/transcript");
  S.segs = t.segments.map((s) => ({ id: s.id, start: s.start, end: s.end, text: s.text }));
  S.nextId = Math.max(0, ...S.segs.map((s) => s.id)) + 1;
  const v = $("#video");
  v.src = S.localUrl || "/api/jobs/" + job.id + "/source";
  $("#frame").hidden = true;
  $("#btn-srt").href = "/api/jobs/" + job.id + "/download/srt";
  $("#view-editor").dataset.loaded = "1"; $("#view-editor").dataset.job = job.id;
  renderSegs(); applyStyleToOverlay();
  $("#save-state").textContent = "";
}

function renderSegs(focusId) {
  const box = $("#segs");
  box.textContent = "";
  $("#empty").hidden = S.segs.length > 0;
  for (const s of S.segs) {
    const row = document.createElement("div");
    row.className = "seg"; row.dataset.id = s.id;
    const top = document.createElement("div"); top.className = "seg-top";
    const a = document.createElement("input"); a.type = "number"; a.step = "0.05"; a.min = "0"; a.value = s.start.toFixed(2); a.dataset.k = "start"; a.setAttribute("aria-label", "start");
    const arrow = document.createElement("span"); arrow.textContent = "→";
    const b = document.createElement("input"); b.type = "number"; b.step = "0.05"; b.min = "0"; b.value = s.end.toFixed(2); b.dataset.k = "end"; b.setAttribute("aria-label", "end");
    const sp = document.createElement("span"); sp.className = "sp";
    const play = document.createElement("button"); play.textContent = "▶"; play.dataset.act = "play"; play.title = "Play from here";
    const del = document.createElement("button"); del.textContent = "✕"; del.dataset.act = "del"; del.title = "Delete";
    top.append(a, arrow, b, sp, play, del);
    const ta = document.createElement("textarea"); ta.value = s.text; ta.rows = 1; ta.lang = "bn"; ta.spellcheck = false; ta.setAttribute("aria-label", "caption text");
    row.append(top, ta);
    box.appendChild(row);
    autosize(ta);
  }
  S.activeIdx = -1;
  if (focusId != null) { const ta = box.querySelector(`.seg[data-id="${focusId}"] textarea`); if (ta) ta.focus(); }
}
function autosize(ta) { ta.style.height = "auto"; ta.style.height = ta.scrollHeight + 2 + "px"; }
const segById = (id) => S.segs.find((s) => String(s.id) === String(id));

function bindEditor() {
  $("#error-close").onclick = clearError;
  $("#btn-new").onclick = newVideo;
  $("#btn-retry").onclick = newVideo;
  const box = $("#segs");
  box.addEventListener("input", (e) => {
    if (e.target.tagName !== "TEXTAREA") return;
    const seg = segById(e.target.closest(".seg").dataset.id);
    if (seg) { seg.text = e.target.value; S.activeIdx = -2; autosize(e.target); scheduleSave(); }
  });
  box.addEventListener("change", (e) => {
    if (e.target.tagName !== "INPUT") return;
    const seg = segById(e.target.closest(".seg").dataset.id);
    const val = parseFloat(e.target.value);
    if (!seg || !isFinite(val) || val < 0) { renderSegs(); return; }
    seg[e.target.dataset.k] = val;
    S.segs.sort((x, y) => x.start - y.start || x.end - y.end);
    renderSegs(); scheduleSave();
  });
  box.addEventListener("click", (e) => {
    const btn = e.target.closest("button"); const row = e.target.closest(".seg");
    if (!row) return;
    const seg = segById(row.dataset.id); if (!seg) return;
    const v = $("#video");
    if (btn && btn.dataset.act === "del") { S.segs = S.segs.filter((x) => x !== seg); renderSegs(); scheduleSave(); }
    else if (btn && btn.dataset.act === "play") { v.currentTime = seg.start; v.play().catch(() => {}); }
    else if (e.target.tagName !== "TEXTAREA" && e.target.tagName !== "INPUT") { v.currentTime = seg.start; }
  });
  $("#btn-add").onclick = () => {
    const v = $("#video"); const dur = S.job ? S.job.duration : v.duration || 1;
    const start = Math.min(v.currentTime || 0, Math.max(0, dur - 0.3));
    const seg = { id: S.nextId++, start: +start.toFixed(2), end: +Math.min(start + 2, dur).toFixed(2), text: "" };
    S.segs.push(seg); S.segs.sort((x, y) => x.start - y.start || x.end - y.end);
    renderSegs(seg.id);
  };
  $("#btn-srt").addEventListener("click", async (e) => {
    e.preventDefault(); clearError();
    if (!(await flushSave())) return;
    window.location.href = $("#btn-srt").href;
  });
  for (const id of ["s-preset", "s-position", "s-size", "s-font", "s-wpc", "s-wm"]) $("#" + id).addEventListener("input", () => { saveStyle(); applyStyleToOverlay(); });
  $("#btn-preview").onclick = doPreview;
  $("#btn-render").onclick = doRender;
  $("#video").addEventListener("error", () => {
    if (S.localUrl && $("#video").src.startsWith("blob:")) { S.localUrl = null; $("#video").src = "/api/jobs/" + S.jobId + "/source"; }
  });
  requestAnimationFrame(tick);
}

/* live overlay + active row (cheap rAF loop) */
function tick() {
  const v = $("#video");
  if (!$("#view-editor").hidden && S.segs.length) {
    const t = v.currentTime;
    let idx = -1;
    for (let i = 0; i < S.segs.length; i++) if (t >= S.segs[i].start && t < S.segs[i].end) { idx = i; break; }
    if (idx !== S.activeIdx) {
      S.activeIdx = idx;
      const rows = $("#segs").children;
      for (let i = 0; i < rows.length; i++) rows[i].classList.toggle("active", i === idx);
      const ov = $("#overlay");
      if (idx >= 0 && S.segs[idx].text.trim()) {
        ov.hidden = false; ov.textContent = "";
        const sp = document.createElement("span"); sp.textContent = S.segs[idx].text; ov.appendChild(sp);
        if (!v.paused && document.activeElement.tagName !== "TEXTAREA") rows[idx].scrollIntoView({ block: "nearest", behavior: "smooth" });
      } else ov.hidden = true;
    }
  }
  requestAnimationFrame(tick);
}

/* ---------- saving ---------- */
function setSaveState(kind, msg) {
  const el = $("#save-state");
  el.textContent = kind === "saving" ? "সেভ হচ্ছে…" : kind === "saved" ? "✓ সেভ হয়েছে" : kind === "error" ? "⚠ সেভ হয়নি: " + msg : "";
  el.style.color = kind === "error" ? "var(--danger)" : kind === "saved" ? "var(--ok)" : "";
}
function scheduleSave() {
  S.dirty = true; setSaveState("saving");
  clearTimeout(S.saveTimer);
  S.saveTimer = setTimeout(() => { flushSave(); }, 700);
}
function flushSave() {
  clearTimeout(S.saveTimer);
  S.saveChain = S.saveChain.then(async () => {
    if (!S.dirty) return true;
    const snapshot = S.segs.map(({ id, start, end, text }) => ({ id, start, end, text }));
    S.dirty = false;
    try {
      await api("/api/jobs/" + S.jobId + "/transcript", jsonOpts("PUT", { segments: snapshot }));
      if (!S.dirty) setSaveState("saved");
      return true;
    } catch (e) {
      S.dirty = true; setSaveState("error", e.message); showError(e.message);
      return false;
    }
  });
  return S.saveChain;
}
window.addEventListener("beforeunload", (e) => { if (S.dirty) { e.preventDefault(); e.returnValue = ""; } });

/* ---------- style ---------- */
const STYLE_IDS = { preset: "s-preset", position: "s-position", size: "s-size", font: "s-font" };
function currentStyle() {
  return {
    preset: $("#s-preset").value, position: $("#s-position").value, size: $("#s-size").value, font: $("#s-font").value,
    words_per_caption: parseInt($("#s-wpc").value, 10), watermark: $("#s-wm").checked,
  };
}
function saveStyle() { lsSet(STORE_STYLE, JSON.stringify(currentStyle())); }
function loadStyle() {
  try {
    const st = JSON.parse(lsGet(STORE_STYLE) || "null");
    if (st) {
      for (const [k, id] of Object.entries(STYLE_IDS)) if (st[k]) $("#" + id).value = st[k];
      if (st.words_per_caption) $("#s-wpc").value = st.words_per_caption;
      if (typeof st.watermark === "boolean") $("#s-wm").checked = st.watermark;
    }
  } catch (_) { /* ignore */ }
}
function applyStyleToOverlay() {
  const s = currentStyle();
  $("#s-wpc-val").textContent = s.words_per_caption;
  $("#overlay").className = `overlay pos-${s.position} pre-${s.preset} size-${s.size} font-${s.font}`;
  $("#overlay").hidden = true; S.activeIdx = -1;
}

/* ---------- preview / render ---------- */
async function doPreview() {
  clearError();
  if (S.busyPreview) return;
  S.busyPreview = true; const btn = $("#btn-preview"); btn.disabled = true; btn.textContent = "⏳ হচ্ছে…";
  try {
    if (!(await flushSave())) return;
    const body = { ...currentStyle(), t: $("#video").currentTime || 0 };
    const r = await fetch("/api/jobs/" + S.jobId + "/preview", jsonOpts("POST", body));
    if (!r.ok) { let d = null; try { d = await r.json(); } catch (_) { /* */ } throw new Error(errText(d, r.status)); }
    const blob = await r.blob();
    if (S.frameUrl) URL.revokeObjectURL(S.frameUrl);
    S.frameUrl = URL.createObjectURL(blob);
    const img = $("#frame"); img.src = S.frameUrl; img.hidden = false;
    img.scrollIntoView({ block: "nearest", behavior: "smooth" });
  } catch (e) { showError(e.message); }
  finally { S.busyPreview = false; btn.disabled = false; btn.textContent = "🖼 Frame preview"; }
}

async function doRender() {
  clearError();
  const btn = $("#btn-render"); btn.disabled = true;
  try {
    if (!(await flushSave())) { btn.disabled = false; return; }
    const job = await api("/api/jobs/" + S.jobId + "/render", jsonOpts("POST", currentStyle()));
    await onJob(job, false);
  } catch (e) { btn.disabled = false; showError(e.message); }
}

function updateRenderUi(job) {
  const r = job.render || { state: "idle" };
  const box = $("#render-box"), bar = $("#render-bar"), msg = $("#render-msg"), dl = $("#btn-download"), btn = $("#btn-render");
  btn.disabled = r.state === "queued" || r.state === "running";
  box.hidden = r.state === "idle";
  dl.hidden = r.state !== "done";
  if (r.state === "queued") { setBar(bar, 0, true); msg.textContent = "লাইনে আছে…"; }
  else if (r.state === "running") { setBar(bar, r.progress || 0); msg.textContent = `ভিডিও বানানো হচ্ছে… ${Math.round(r.progress || 0)}%`; }
  else if (r.state === "done") { setBar(bar, 100); msg.textContent = "✓ তৈরি! নিচের বাটনে ডাউনলোড করো।"; dl.href = "/api/jobs/" + job.id + "/download/video?v=" + Date.now(); }
  else if (r.state === "failed") { setBar(bar, 0); msg.textContent = "✗ " + (r.error || "Render fail"); }
}

async function newVideo() {
  if (S.dirty && !confirm("Save na kora edit ache. Tobuo nothun video shuru korbe?")) return;
  stopPoll(); clearTimeout(S.saveTimer); S.dirty = false;
  const id = S.jobId;
  S.jobId = null; S.job = null; S.segs = []; lsSet(STORE_JOB, null);
  if (id) { try { await fetch("/api/jobs/" + id, { method: "DELETE" }); } catch (_) { /* ignore */ } }
  const v = $("#video"); v.removeAttribute("src"); v.load();
  if (S.localUrl) { URL.revokeObjectURL(S.localUrl); S.localUrl = null; }
  delete $("#view-editor").dataset.loaded; delete $("#view-editor").dataset.job;
  clearError(); show("upload");
}

init();
