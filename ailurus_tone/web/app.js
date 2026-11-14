"use strict";
const $ = (id) => document.getElementById(id);
const LANG = (navigator.language || "en").toLowerCase().startsWith("cs") ? "cs" : "en";
let HELP = {}, state = null, patches = [], patchesFor = null, selFx = null, fxData = null, lastVersion = -1;
const EMPTY = "Plug in a supported unit and quit BOSS TONE STUDIO.";
let dragging = null; // {fx, param, old}

async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} :
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || r.statusText);  // /api/state carries a normal "error" field (e.g. unplugged)
  return j;
}
function esc(s) { return String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
function flash(msg) { const m = $("message"); delete m.dataset.conn; m.textContent = msg; m.classList.remove("hidden"); clearTimeout(flash.t); flash.t = setTimeout(() => m.classList.add("hidden"), 6000); }
function helpFor(kind, key) { return (HELP[LANG]?.[kind]?.[key]) || (HELP.en?.[kind]?.[key]) || ""; }

// ---- tooltips: short, after a delay, also on keyboard focus ------------------
let tipTimer;
function showTip(el) {
  const text = el.dataset.help; if (!text) return;
  clearTimeout(tipTimer);
  tipTimer = setTimeout(() => {
    const t = $("tip"); t.textContent = text; t.classList.remove("hidden");
    const r = el.getBoundingClientRect();
    let top = r.bottom + 6, left = Math.min(r.left, innerWidth - 280);
    if (top + 80 > innerHeight) top = r.top - t.offsetHeight - 6;  // never cover from below the window
    t.style.top = top + "px"; t.style.left = Math.max(8, left) + "px";
  }, 450);
}
function hideTip() { clearTimeout(tipTimer); $("tip").classList.add("hidden"); }
document.addEventListener("mouseover", (e) => { const el = e.target.closest("[data-help]"); el ? showTip(el) : hideTip(); });
document.addEventListener("focusin", (e) => { const el = e.target.closest("[data-help]"); if (el) showTip(el); });
document.addEventListener("mousedown", hideTip);

// ---- rendering ---------------------------------------------------------------
function renderTop() {
  const c = state?.connected;
  $("conn").className = "conn " + (c ? "on" : "off");
  $("connText").textContent = c ? `${state.device_title} connected` : "Not connected";
  $("patchLabel").textContent = c ? `${state.patch.label}  ${state.patch.name}` : "";
  $("edited").classList.toggle("hidden", !(c && state.patch.edited));
  $("readonly").classList.toggle("hidden", !(c && state.patch.readonly));
  $("undoBtn").disabled = !(c && state.patch.can_undo);
  $("redoBtn").disabled = !(c && state.patch.can_redo);
  const busy = !!state?.backup?.running;
  $("saveBtn").disabled = !(c && state.patch.edited && state.patch.can_save) || busy;
  $("saveAsBtn").disabled = !(c && (state.writable || []).length) || busy;
  $("irBtn").classList.toggle("hidden", !(c && state.has_ir));
  $("irBtn").disabled = busy;
  for (const id of ["backupBtn", "exportBtn"]) $(id).disabled = !c || !!state.backup?.running || !state.can_backup;
  renderDevices();
  // a connection problem stays shown until a unit is connected; a flash() message keeps its own timer
  const m = $("message");
  if (state?.error && !c) { m.textContent = state.error; m.classList.remove("hidden"); m.dataset.conn = "1"; }
  else if (c && m.dataset.conn) { m.classList.add("hidden"); delete m.dataset.conn; }
  const b = state?.backup;
  $("backupState").innerHTML = !b ? "" : b.running ? `Backing up… ${b.done}/${b.total}`
    : b.error ? `<span class="err">Backup failed: ${esc(b.error)}</span>`
    : b.path ? `Backup saved (${b.seconds} s): ${esc(b.path)}` : "";
}

// dropdown of the set-up units; shown once there is a choice to make
function renderDevices() {
  const list = (state?.devices || []).filter((d) => !d.missing);
  const box = $("devices");
  const html = list.map((d) => `<option value="${esc(d.id)}" ${d.active ? "selected" : ""} ${d.connected ? "" : "disabled"}
      title="${esc(d.connected ? `Edit the ${d.title}${d.readonly ? " (read only for now)" : ""}.` : d.error || `${d.title} is not connected. ${d.hint || ""}`.trim())}">
      ${esc(d.title)}${d.connected ? "" : " (not connected)"}</option>`).join("");
  box.classList.toggle("hidden", list.length < 2);
  // rebuild only on a real change, so an open dropdown is not closed by the next poll
  if (box.dataset.html !== html) { box.innerHTML = html; box.dataset.html = html; }
}

function renderPatches() {
  const q = $("search").value.trim().toLowerCase();
  const cur = state?.patch?.current;
  $("patchList").innerHTML = patches.filter((p) => !q || p.name.toLowerCase().includes(q) || p.label.toLowerCase().includes(q))
    .map((p) => `<div class="prow ${p.slot === cur ? "sel" : ""}" data-slot="${p.slot}"><span class="lbl">${p.label}</span><span>${esc(p.name)}</span></div>`).join("");
}

function renderChain() {
  const chain = state?.connected ? state.patch.chain : [];
  if (chain.length && !chain.some((b) => b.fx === selFx)) selFx = chain[0].fx;
  const movable = !!state?.patch?.straight;
  $("chain").classList.toggle("movable", movable);
  $("chain").innerHTML = chain.map((b, i) =>
    (i ? (b.in_chain === false ? '<span class="arrow sep" data-help="Not part of the signal order shown">|</span>' : '<span class="arrow">›</span>') : "") +
    `<div class="blk c-${esc(b.color)} ${b.on ? "" : "off"} ${b.fx === selFx ? "sel" : ""}" data-fx="${b.fx}" tabindex="0"
       draggable="${movable}" data-help="${esc(helpFor("types", b.type))}${movable ? " Drag to move it in the chain." : ""}">
       <div class="t">${esc(b.type)}</div><div class="n">${b.sub != null ? esc(b.sub) || "&nbsp;" : "fx " + b.fx}</div></div>`).join("");
}

function renderFx() {
  const d = fxData;
  if (!d) { $("fxPanel").className = "fx"; $("fxPanel").innerHTML = `<div class="empty muted">${EMPTY}</div>`; return; }
  const sw = d.params.find((p) => p.name === "OFF/ON");
  const ro = d.readonly ? "disabled" : "";
  const rows = d.params.filter((p) => p !== sw).map((p) => {
    const h = esc(helpFor("params", p.name));
    const dot = p.changed ? `<span class="dot" data-help="Changed. Saved value: ${esc(p.saved)}"></span>` : "";
    const ctl = p.options
      ? `<select data-param="${esc(p.name)}" ${ro}>${p.options.map((o, i) => `<option value="${i + p.min}" ${i + p.min === p.value ? "selected" : ""}>${esc(o)}</option>`).join("")}</select>`
      : `<input type="range" min="${p.min}" max="${p.max}" step="1" value="${p.value}" data-param="${esc(p.name)}" ${ro}>`;
    // typed entry: the raw number; the formatted text (e.g. "+10dB", "420 ms") sits next to it when it differs
    const shown = String(p.display) === String(p.value) ? "" : esc(p.display);
    const num = p.options ? "" : `<div class="vrow">
        <input class="num" type="number" min="${p.min}" max="${p.max}" step="1" value="${p.value}" ${ro}
               data-num="${esc(p.name)}" aria-label="${esc(p.name)} value" data-help="Type a value ${p.min} to ${p.max}, then Enter.">
        <span class="v" data-v="${esc(p.name)}">${shown}</span></div>`;
    return `<div class="prm"><label data-help="${h}">${esc(p.name)} ${dot}</label>${ctl}${num}</div>`;
  }).join("");
  $("fxPanel").className = `fx c-${esc(d.color)}`;
  $("fxPanel").innerHTML = `<div class="fxhead"><span class="swatch"></span><span class="t" data-help="${esc(helpFor("types", d.type))}">${esc(d.type)}</span>
      <span class="muted">${d.sub != null ? esc(d.sub) : "fx " + d.fx}</span><span class="grow"></span>
      ${sw ? `<label class="switch" data-help="${esc(helpFor("params", "OFF/ON"))}"><input type="checkbox" id="fxOn" ${d.on ? "checked" : ""} ${ro}> On</label>` : ""}</div>
    <div class="params">${rows || '<span class="muted">No editable parameters.</span>'}</div>`;
}

const WHO = { you: "You", ai: "AI", unit: "Unit" };
const WHO_HELP = { you: "You, in this window", ai: "An AI through MCP", unit: "A knob or switch on the unit itself" };

function describeEvent(e) {
  const dev = e.device && e.device !== state?.device_title ? `<span class="act">${esc(e.device)}</span> ` : "";
  if (e.action === "patch") return `${dev}→ ${esc(e.patch)} ${esc(e.name)}`;
  if (e.action === "ir_load") return `${dev}<span class="act">IR loaded</span> ${esc(e.patch)} ${esc(e.name)}`;
  if (e.action === "ir_rename") return `${dev}<span class="act">IR renamed</span> ${esc(e.patch)} ${esc(e.old)} → ${esc(e.name)}`;
  if (e.action === "save") return `${dev}<span class="act">saved</span> ${esc(e.patch)} ${esc(e.name)}`;
  if (e.param === "CHAIN") {
    const verb = e.action === "set" ? "" : `<span class="act">${e.action}</span> `;
    return `${verb}${esc(e.display)}`;
  }
  const shown = e.display != null && String(e.display) !== String(e.new) ? ` (${esc(e.display)})` : "";
  const what = `${esc(e.type || "fx" + e.fx)} ${esc(e.param)} ${esc(e.old)} → ${esc(e.new)}${shown}`;
  if (e.action === "undo") return `<span class="act">undo</span> ${what}`;
  if (e.action === "redo") return `<span class="act">redo</span> ${what}`;
  return what;
}

function renderChanges() {
  const list = state?.history || [];
  $("changeList").innerHTML = list.length ? list.map((e) => {
    const time = new Date(e.t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
    return `<div class="chg ${e.action}"><span class="who ${esc(e.who)}" data-help="${WHO_HELP[e.who] || ""}">${WHO[e.who] || esc(e.who)}</span>
     <span class="grow">${describeEvent(e)}</span><span class="tm">${time}</span></div>`;
  }).join("")
    : '<div class="small muted">Nothing yet. Every change, undo and patch switch lands here, and the AI can read it.</div>';
}

// ---- data flow -----------------------------------------------------------------
async function loadFx() {
  if (!state?.connected || !selFx) { fxData = null; renderFx(); return; }
  try { fxData = await api(`/api/fx/${selFx}`); } catch (e) { fxData = null; }
  const typing = document.activeElement?.dataset?.num !== undefined;
  if (!dragging && !typing) renderFx();
}

async function poll() {
  try {
    const s = await api("/api/state");
    const wasConnected = state?.connected, prevPatch = state?.patch?.current;
    state = s;
    if (s.device !== patchesFor) { patches = []; selFx = null; fxData = null; lastVersion = -1; }
    if (s.connected && (!wasConnected || !patches.length || patchesFor !== s.device)) {
      const r = await api("/api/patches"); patches = r.patches; patchesFor = r.device;
    }
    if (!s.connected) patchesFor = s.device;
    renderTop(); renderUpdate(s.update);
    if (s.version !== lastVersion || s.patch?.current !== prevPatch) {
      lastVersion = s.version;
      renderPatches(); renderChain(); renderChanges(); await loadFx();
    }
  } catch (e) { /* server restarting */ }
  setTimeout(poll, 700);
}

// ---- events ----------------------------------------------------------------------
$("chain").addEventListener("click", (e) => {
  const b = e.target.closest(".blk"); if (!b) return;
  selFx = +b.dataset.fx; renderChain(); loadFx();
});
$("chain").addEventListener("keydown", (e) => { if (e.key === "Enter") e.target.closest(".blk")?.click(); });
$("search").addEventListener("input", renderPatches);
$("devices").addEventListener("change", async (e) => {
  const sel = e.target, prev = state?.device;
  if (sel.value === prev) return;
  if (state?.patch?.edited && !confirm("The other unit keeps its sound; this one keeps your unsaved changes too. Switch the view?")) {
    sel.value = prev; return;
  }
  try { await api("/api/device", { id: sel.value }); selFx = null; patches = []; lastVersion = -1; }
  catch (err) { sel.value = prev; flash(err.message); }
});
$("patchList").addEventListener("click", async (e) => {
  const r = e.target.closest(".prow"); if (!r) return;
  if (state?.patch?.edited && !confirm("Switching patch discards your unsaved changes. Continue?")) return;
  try { await api("/api/select", { slot: +r.dataset.slot }); selFx = null; } catch (err) { flash(err.message); }
});

let sendTimer = null;
$("fxPanel").addEventListener("input", (e) => {
  const el = e.target; if (el.type !== "range") return;
  const name = el.dataset.param, val = +el.value;
  if (!dragging) dragging = { fx: fxData.fx, param: name, old: fxData.params.find((p) => p.name === name).value };
  clearTimeout(sendTimer);
  const box = document.querySelector(`[data-num="${CSS.escape(name)}"]`); if (box) box.value = val;
  sendTimer = setTimeout(async () => {
    try {
      const r = await api("/api/param", { fx: fxData.fx, param: name, value: val, record: false });
      showDisplay(name, r);
    } catch (err) { flash(err.message); }
  }, 25);
});

function showDisplay(name, r) {
  const v = document.querySelector(`[data-v="${CSS.escape(name)}"]`);
  if (v) v.textContent = String(r.display) === String(r.new) ? "" : r.display;
}

// typed value: Enter (or leaving the field) sends it as ONE change; Escape restores
$("fxPanel").addEventListener("keydown", (e) => {
  const el = e.target; if (!el.dataset?.num) return;
  if (e.key === "Enter") { e.preventDefault(); el.blur(); }
  if (e.key === "Escape") { el.value = fxData.params.find((p) => p.name === el.dataset.num).value; el.classList.remove("bad"); el.blur(); }
});
$("fxPanel").addEventListener("focusout", async (e) => {
  const el = e.target; if (!el.dataset?.num) return;
  const p = fxData.params.find((x) => x.name === el.dataset.num);
  const val = Number(el.value);
  if (el.value.trim() === "" || !Number.isInteger(val) || val < p.min || val > p.max) {
    el.classList.add("bad"); flash(`${p.name}: enter a whole number ${p.min} to ${p.max}`); el.value = p.value; return;
  }
  el.classList.remove("bad");
  if (val === p.value) return;
  try {
    const r = await api("/api/param", { fx: fxData.fx, param: p.name, value: val });
    p.value = val;
    const s = document.querySelector(`input[type=range][data-param="${CSS.escape(p.name)}"]`); if (s) s.value = val;
    showDisplay(p.name, r);
  } catch (err) { flash(err.message); el.value = p.value; }
});
$("fxPanel").addEventListener("change", async (e) => {
  const el = e.target;
  try {
    if (el.id === "fxOn") { await api("/api/switch", { fx: fxData.fx, on: el.checked }); return; }
    if (el.tagName === "SELECT") { await api("/api/param", { fx: fxData.fx, param: el.dataset.param, value: +el.value }); return; }
    if (el.type === "range" && dragging) {
      // knob released: make sure the final value is sent, then store ONE history entry
      clearTimeout(sendTimer);
      await api("/api/param", { fx: dragging.fx, param: dragging.param, value: +el.value, record: false });
      await api("/api/record", { fx: dragging.fx, param: dragging.param, old: dragging.old, new: +el.value });
    }
  } catch (err) { flash(err.message); } finally { dragging = null; lastVersion = -1; }
});

async function undo() { try { await api("/api/undo", {}); } catch (e) { flash(e.message); } }
async function redo() { try { await api("/api/redo", {}); } catch (e) { flash(e.message); } }
$("undoBtn").onclick = undo; $("redoBtn").onclick = redo;
document.addEventListener("keydown", (e) => {
  const mod = e.metaKey || e.ctrlKey; if (!mod || e.target.matches("input[type=text], input[type=search]")) return;
  if (e.key.toLowerCase() === "z" && !e.shiftKey) { e.preventDefault(); undo(); }
  else if ((e.key.toLowerCase() === "z" && e.shiftKey) || e.key.toLowerCase() === "y") { e.preventDefault(); redo(); }
});

async function pickFolder(start) {
  if (window.pywebview?.api?.pick_folder) return await window.pywebview.api.pick_folder(start || "");
  return prompt("Folder path:", start || "");
}
$("backupBtn").onclick = async () => { try { await api("/api/backup", {}); } catch (e) { flash(e.message); } };
$("exportBtn").onclick = async () => {
  const last = localStorage.getItem("lastExportDir") || state?.backup_dir || "";
  const dir = await pickFolder(last); if (!dir) return;
  try { localStorage.setItem("lastExportDir", dir); } catch (e) {}
  try { await api("/api/backup", { folder: dir }); } catch (e) { flash(e.message); }
};
// ---- saving ------------------------------------------------------------------------
function slotText(w) { return `${w.label}  ${w.name || "(empty)"}`; }
async function doSave(slot, name) {
  await api("/api/save", name == null ? { slot } : { slot, name });
  lastVersion = -1; patches = [];
}
$("saveBtn").onclick = async () => {
  const p = state.patch;
  if (!confirm(`Store what you hear as ${p.label} "${p.name}"? The patch stored there now is backed up first.`)) return;
  try { await doSave(p.current); flash(`Saved ${p.label} ${p.name}.`); } catch (e) { flash(e.message); }
};
function saveWarn() {
  const w = (state.writable || []).find((x) => x.slot === +$("saveSlot").value);
  $("saveWarn").textContent = w ? `Overwrites ${slotText(w)} (backed up first).` : "";
}
$("saveAsBtn").onclick = () => {
  const ws = state.writable || [];
  $("saveSlot").innerHTML = ws.map((w) => `<option value="${w.slot}">${esc(slotText(w))}</option>`).join("");
  if (ws.some((w) => w.slot === state.patch.current)) $("saveSlot").value = state.patch.current;
  $("saveName").value = state.patch.name || ""; $("saveErr").textContent = ""; saveWarn();
  $("saveDlg").showModal(); $("saveName").focus();
};
$("saveSlot").onchange = saveWarn;
$("saveGo").onclick = async () => {
  const slot = +$("saveSlot").value, name = $("saveName").value.trim();
  if (!name) { $("saveErr").textContent = "Give the patch a name."; return; }
  $("saveGo").disabled = true;
  try { await doSave(slot, name); $("saveDlg").close(); flash("Saved."); }
  catch (e) { $("saveErr").textContent = e.message; }
  finally { $("saveGo").disabled = false; }
};
// ---- GX-10 cabinet IRs --------------------------------------------------------------
const IR_NAME_LEN = 12;
function irDefaultName(file) {
  return file.replace(/\.[^.]*$/, "").replace(/[^\x20-\x7e]/g, "").trim().slice(0, IR_NAME_LEN).trim();
}
async function loadIrs() {
  const box = $("irList");
  try {
    const { slots } = await api("/api/ir");
    box.innerHTML = slots.map((s) => `<div class="orow"><span class="grow"><b>${esc(s.label)}</b>
      ${s.empty ? '<span class="muted">empty</span>' : esc(s.name)}
      ${s.source ? `<span class="muted">· ${esc(s.source)}</span>` : ""}</span>
      <button type="button" data-load="${s.slot}">Load .wav…</button>
      <button type="button" data-rename="${s.slot}"${s.empty ? " disabled" : ""}>Rename</button></div>`).join("");
    box.querySelectorAll("button[data-load]").forEach((b) => (b.onclick = () => irLoad(slots[+b.dataset.load])));
    box.querySelectorAll("button[data-rename]").forEach((b) => (b.onclick = () => irRename(slots[+b.dataset.rename])));
  } catch (e) { box.textContent = e.message; }
}
function irBusy(on) { $("irList").querySelectorAll("button").forEach((b) => (b.disabled = on)); }
function irLoad(s) {
  const inp = document.createElement("input");
  inp.type = "file"; inp.accept = ".wav,audio/wav";
  inp.onchange = async () => {
    const f = inp.files[0]; if (!f) return;
    const name = (prompt(`Name for ${s.label} (up to ${IR_NAME_LEN} characters):`, irDefaultName(f.name)) || "").trim();
    if (!name) return;
    if (!s.empty && !confirm(`${s.label} holds "${s.name}". The unit cannot give it back, so it cannot be backed up. Replace it with ${f.name}?`)) return;
    irBusy(true); $("irErr").textContent = ""; $("irList").insertAdjacentHTML("afterbegin", '<div class="muted irwait">Converting and sending…</div>');
    try {
      const q = `slot=${s.slot}&file=${encodeURIComponent(f.name)}&name=${encodeURIComponent(name)}${s.empty ? "" : "&replace=1"}`;
      const r = await fetch(`/api/ir/load?${q}`, { method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: f });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j.error || r.statusText);
      flash(`${s.label} "${j.name}" loaded (fit within ${j.error_db} dB of the file).`); lastVersion = -1;
    } catch (e) { $("irErr").textContent = e.message; }
    loadIrs();
  };
  inp.click();
}
async function irRename(s) {
  const name = (prompt(`New name for ${s.label} (up to ${IR_NAME_LEN} characters):`, s.name) || "").trim();
  if (!name || name === s.name) return;
  irBusy(true); $("irErr").textContent = "";
  try { await api("/api/ir/rename", { slot: s.slot, name }); lastVersion = -1; }
  catch (e) { $("irErr").textContent = e.message; }
  loadIrs();
}
$("irBtn").onclick = () => { $("irErr").textContent = ""; $("irList").textContent = "Loading…"; $("irDlg").showModal(); loadIrs(); };

async function loadOverwritten() {
  const box = $("overwrittenList");
  if (!state?.connected) { box.innerHTML = '<span class="muted">Connect a unit to see its replaced patches.</span>'; return; }
  try {
    const { items } = await api("/api/overwritten");
    box.innerHTML = items.length ? items.map((o) => `<div class="orow"><span class="grow">${esc(o.label)} ${esc(o.name)}
      <span class="muted">· replaced ${esc((o.created || "").replace("T", " ").slice(0, 16))}</span></span>
      <button type="button" data-file="${esc(o.file)}">Put back</button></div>`).join("")
      : '<span class="muted">None yet.</span>';
    box.querySelectorAll("button[data-file]").forEach((b) => (b.onclick = async () => {
      const o = items.find((x) => x.file === b.dataset.file);
      if (!confirm(`Put "${o.name}" back into ${o.label}? What you hear now is replaced by it, and what ${o.label} holds now is backed up first.`)) return;
      b.disabled = true;
      try { await api("/api/putback", { file: o.file }); lastVersion = -1; patches = []; loadOverwritten(); }
      catch (e) { $("settingsErr").textContent = e.message; b.disabled = false; }
    }));
  } catch (e) { box.textContent = e.message; }
}

$("settingsBtn").onclick = () => {
  $("backupDir").value = state?.backup_dir || ""; $("settingsErr").textContent = "";
  $("aiCanSave").checked = !!state?.ai_can_save; loadOverwritten();
  renderBts(state?.bts || []); showTab("general"); $("settings").showModal();
};
function showTab(name) {
  document.querySelectorAll("#settings .tab").forEach((t) => t.classList.toggle("sel", t.dataset.tab === name));
  document.querySelectorAll("#settings .pane").forEach((p) => p.classList.toggle("hidden", p.dataset.pane !== name));
  $("saveSettings").classList.toggle("hidden", name !== "general");
  if (name === "about") loadAbout();
}
document.querySelectorAll("#settings .tab").forEach((t) => (t.onclick = () => showTab(t.dataset.tab)));
async function loadAbout() {
  try {
    const a = await api("/api/about");
    const link = (u, t) => `<a href="${esc(u)}" target="_blank" rel="noopener">${esc(t)}</a>`;
    $("aboutBox").innerHTML = `<p><b>Ailurus Tone Editor ${esc(a.version)}</b> <span class="muted">(build ${esc(a.build)}, ${esc(a.platform)})</span></p>
      <p>Live editor and MCP server for BOSS units: GX-10 and KATANA Gen 3. Edit your tone by hand, or let an AI do it.</p>
      <p>${a.links.map((l) => link(l.url, l.title)).join(" · ")}</p>
      ${a.can_update ? `<p><button type="button" id="checkUpdates">Check for updates</button> <span class="muted">Checked once a day by itself.</span><br><span id="updateResult"></span></p>` : ""}
      <p>MCP endpoint while the app runs: <code>${esc(a.mcp_url)}</code><br>
        <span class="muted">Claude Code:</span> <code>claude mcp add --transport http ailurus-tone ${esc(a.mcp_url)}</code></p>
      ${a.can_quit ? `<p><button type="button" id="quitApp">Quit Ailurus Tone Editor</button>
        <span class="muted">The app runs in the background while this page is closed; the AI can still reach it.</span></p>` : ""}
      <p>Licensed under ${link(a.licence.url, a.licence.title)}.<br><span class="muted">${esc(a.notice)}</span></p>
      <p class="muted">Built with ${a.libraries.map((l) => `${link(l.url, l.name)} (${esc(l.licence)})`).join(" · ")}</p>`;
    const q = $("quitApp");
    if (q) q.onclick = async () => {
      try { await api("/api/quit", {}); document.body.innerHTML = "<p style='padding:2em'>Ailurus Tone Editor has quit. You can close this page.</p>"; }
      catch (e) { flash(e.message); }
    };
    const b = $("checkUpdates");
    if (b) b.onclick = async () => {
      try {
        const r = await api("/api/update/check", {});
        if (r.latest !== undefined) {           // Linux: the answer comes back to the page
          $("updateResult").innerHTML = r.newer ? updateHtml(r) : `You have the latest version (${esc(r.latest)}).`;
        }
      } catch (e) { flash(e.message); }
    };
  } catch (e) { $("aboutBox").textContent = e.message; }
}

// Linux: a newer release exists (the AppImage does not update itself)
function updateHtml(u) {
  return `Ailurus Tone Editor ${esc(u.latest)} is available. <a href="${esc(u.url)}" target="_blank"
    rel="noopener">Download it</a> and replace this AppImage with it.`;
}
function renderUpdate(u) {
  const n = $("updateNote");
  if (!u || localStorage.getItem("dismissedUpdate") === u.latest) { n.classList.add("hidden"); return; }
  if (n.dataset.v !== u.latest) {
    n.dataset.v = u.latest;
    n.innerHTML = `${updateHtml(u)} <button type="button" id="dismissUpdate">Later</button>`;
    $("dismissUpdate").onclick = () => { try { localStorage.setItem("dismissedUpdate", u.latest); } catch (e) {} n.classList.add("hidden"); };
  }
  n.classList.remove("hidden");
}

// one row per supported unit: where its BOSS TONE STUDIO is (chosen folder or found by itself)
function renderBts(list, errors = {}) {
  $("btsRows").innerHTML = list.map((b) => {
    const st = errors[b.id] ? `<span class="err">${esc(errors[b.id])}</span>`
      : b.found ? `Found, version ${esc(b.version || "?")}: <span class="path">${esc(b.found)}</span>`
      : `<span class="err">${esc(b.error || "Not found.")}</span>`;
    return `<div class="bts" data-id="${esc(b.id)}">
      <div class="small"><b>${esc(b.title)}</b></div>
      <div class="row"><input type="text" readonly value="${esc(b.chosen)}" placeholder="Automatic (usual install place)">
        <button type="button" data-act="pick">Choose…</button>
        <button type="button" data-act="auto" ${b.chosen ? "" : "disabled"}>Automatic</button></div>
      ${b.installer ? `<div class="small muted">There is no BOSS TONE STUDIO for Linux; the app uses the
        Windows one (it only reads its tables, nothing is run).</div>
        ${b.download ? `<div class="row small"><label><input type="checkbox" data-act="agree"> I accept
          <a href="${esc(b.licence_page)}" target="_blank" rel="noopener">Roland's software licence agreement</a></label>
          <button type="button" data-act="download" disabled>Download from Roland</button></div>` : ""}
        <div class="row small"><span class="muted">Or give its installer (.exe or .zip from boss.info):</span>
        <button type="button" data-act="installer">Use installer…</button></div>` : ""}
      <div class="small muted st">${st}</div></div>`;
  }).join("");
}
$("btsRows").addEventListener("change", (e) => {
  if (e.target.dataset.act !== "agree") return;
  e.target.closest(".bts").querySelector('button[data-act="download"]').disabled = !e.target.checked;
});
$("btsRows").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-act]"); if (!btn) return;
  const row = btn.closest(".bts"), id = row.dataset.id;
  let path = "";
  if (btn.dataset.act === "installer") { pickInstaller(row, id); return; }
  if (btn.dataset.act === "download") {
    row.querySelectorAll("button, input").forEach((b) => (b.disabled = true));
    row.querySelector(".st").textContent = "Downloading from Roland and unpacking (about 30 MB)…";
    try { const r = await api("/api/bts/download", { id, agreed: true }); state.bts = r.bts; renderBts(r.bts); lastVersion = -1; }
    catch (err) { renderBts(state?.bts || [], { [id]: err.message }); }
    return;
  }
  if (btn.dataset.act === "pick") {
    path = await pickFolder(row.querySelector("input").value || ""); if (!path) return;
  }
  btn.disabled = true; row.querySelector(".st").textContent = "Loading tables…";
  try { const r = await api("/api/bts", { id, path }); state.bts = r.bts; renderBts(r.bts); lastVersion = -1; }
  catch (err) { renderBts(state?.bts || [], { [id]: err.message }); }
});
// Linux: upload the BTS Windows installer; the app unpacks it (innoextract) and reads it from there
function pickInstaller(row, id) {
  const inp = document.createElement("input");
  inp.type = "file"; inp.accept = ".exe,.zip";
  inp.onchange = async () => {
    const f = inp.files[0]; if (!f) return;
    row.querySelectorAll("button").forEach((b) => (b.disabled = true));
    row.querySelector(".st").textContent = "Unpacking the installer…";
    try {
      const r = await fetch(`/api/bts/installer?id=${encodeURIComponent(id)}&name=${encodeURIComponent(f.name)}`,
        { method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: f });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j.error || r.statusText);
      state.bts = j.bts; renderBts(j.bts); lastVersion = -1;
    } catch (err) { renderBts(state?.bts || [], { [id]: err.message }); }
  };
  inp.click();
}
$("pickDir").onclick = async () => { const d = await pickFolder($("backupDir").value); if (d) $("backupDir").value = d; };
$("saveSettings").onclick = async () => {
  try {
    await api("/api/config", { backup_dir: $("backupDir").value, ai_can_save: $("aiCanSave").checked });
    $("settings").close(); lastVersion = -1;
  }
  catch (e) { $("settingsErr").textContent = e.message; }
};

(async () => { try { HELP = await api("/api/help"); } catch (e) {} poll(); })();

// ---- reorder the chain by drag and drop (straight chains only) -------------------
let dragFx = null;
function dropTarget(e) {
  const blk = e.target.closest?.(".blk");
  if (!blk || +blk.dataset.fx === dragFx) return null;
  const r = blk.getBoundingClientRect();
  return { fx: +blk.dataset.fx, after: e.clientX > r.left + r.width / 2, el: blk };
}
function clearDrop() { document.querySelectorAll(".drop-before, .drop-after, .dragging").forEach((x) => x.classList.remove("drop-before", "drop-after", "dragging")); }
$("chain").addEventListener("dragstart", (e) => {
  const blk = e.target.closest(".blk");
  if (!blk || !state?.patch?.straight) { e.preventDefault(); return; }
  dragFx = +blk.dataset.fx; blk.classList.add("dragging");
  e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", String(dragFx));
});
$("chain").addEventListener("dragover", (e) => {
  if (dragFx == null) return;
  const t = dropTarget(e);
  document.querySelectorAll(".drop-before, .drop-after").forEach((x) => x.classList.remove("drop-before", "drop-after"));
  if (!t) return;
  e.preventDefault(); e.dataTransfer.dropEffect = "move";
  t.el.classList.add(t.after ? "drop-after" : "drop-before");
});
$("chain").addEventListener("drop", async (e) => {
  const t = dropTarget(e);
  const moving = dragFx;
  dragFx = null; clearDrop();
  if (!t || moving == null) return;
  e.preventDefault();
  const order = state.patch.chain.map((b) => b.fx).filter((fx) => fx !== moving);
  order.splice(order.indexOf(t.fx) + (t.after ? 1 : 0), 0, moving);
  try { await api("/api/chain", { order }); } catch (err) { flash(err.message); }
  lastVersion = -1;
});
$("chain").addEventListener("dragend", () => { dragFx = null; clearDrop(); });
