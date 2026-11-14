"use strict";
const $ = (id) => document.getElementById(id);
const LANG = (navigator.language || "en").toLowerCase().startsWith("cs") ? "cs" : "en";
let HELP = {}, state = null, patches = [], patchesFor = null, selFx = null, fxData = null, lastVersion = -1;
const EMPTY = "Plug in the GX-10 or the KATANA and quit BOSS TONE STUDIO.";
let dragging = null; // {fx, param, old}

async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} :
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || r.statusText);  // /api/state carries a normal "error" field (e.g. unplugged)
  return j;
}
function esc(s) { return String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
function flash(msg) { const m = $("message"); m.textContent = msg; m.classList.remove("hidden"); clearTimeout(flash.t); flash.t = setTimeout(() => m.classList.add("hidden"), 6000); }
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
  for (const id of ["backupBtn", "exportBtn"]) $(id).disabled = !c || !!state.backup?.running || state.device !== "gx10";
  renderDevices();
  if (state?.error && !c) { $("message").textContent = state.error; $("message").classList.remove("hidden"); }
  else if (c && $("message").textContent === state?.error) $("message").classList.add("hidden");
  const b = state?.backup;
  $("backupState").innerHTML = !b ? "" : b.running ? `Backing up… ${b.done}/${b.total}`
    : b.error ? `<span class="err">Backup failed: ${esc(b.error)}</span>`
    : b.path ? `Backup saved (${b.seconds} s): ${esc(b.path)}` : "";
}

// one button per set-up unit; shown once there is a choice to make (or a unit is missing its BTS)
function renderDevices() {
  const list = (state?.devices || []).filter((d) => !d.missing);
  const box = $("devices");
  const html = list.length < 2 ? "" : list.map((d) => `<button class="dev ${d.active ? "sel" : ""} ${d.connected ? "" : "off"}" data-dev="${esc(d.id)}"
      role="tab" aria-selected="${d.active}" ${d.connected ? "" : "disabled"}
      data-help="${esc(d.connected ? `Edit the ${d.title}${d.readonly ? " (read only for now)" : ""}.` : d.error || `${d.title} is not connected.`)}">
      <span class="conn ${d.connected ? "on" : "off"}"></span>${esc(d.title)}</button>`).join("");
  if (box.dataset.html !== html) { box.innerHTML = html; box.dataset.html = html; }  // keep the buttons alive between polls
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
    (i ? '<span class="arrow">›</span>' : "") +
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
    renderTop();
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
$("devices").addEventListener("click", async (e) => {
  const b = e.target.closest(".dev"); if (!b || b.classList.contains("sel")) return;
  if (state?.patch?.edited && !confirm("The other unit keeps its sound; this one keeps your unsaved changes too. Switch the view?")) return;
  try { await api("/api/device", { id: b.dataset.dev }); selFx = null; patches = []; lastVersion = -1; } catch (err) { flash(err.message); }
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
$("settingsBtn").onclick = () => { $("backupDir").value = state?.backup_dir || ""; $("settingsErr").textContent = ""; $("settings").showModal(); };
$("pickDir").onclick = async () => { const d = await pickFolder($("backupDir").value); if (d) $("backupDir").value = d; };
$("saveSettings").onclick = async () => {
  try { await api("/api/config", { backup_dir: $("backupDir").value }); $("settings").close(); }
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
