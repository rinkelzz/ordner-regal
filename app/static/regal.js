"use strict";
// Ordner-Regal: Regalansicht, geöffneter Ordner, Datei-Auswahl, Suche, Freigaben-Verwaltung.
const R = { shelves: [], shelfId: null, shelf: null, binder: null, tabId: null, docId: null, tags: [], tagFilter: null,
            canCreate: false, hasShares: true, blob: null };
const COLORS = ["#c53030", "#dd6b20", "#d69e2e", "#2f855a", "#2c7a7b", "#2b6cb0", "#2c5282", "#6b46c1", "#b83280", "#4a5568", "#1a202c", "#975a16"];
const TAB_COLORS = ["", "#e53e3e", "#dd6b20", "#ecc94b", "#38a169", "#3182ce", "#805ad5", "#d53f8c", "#718096"];
const fmtBytes = (b) => b >= 1048576 ? (b / 1048576).toFixed(1).replace(".", ",") + " MB" : b >= 1024 ? Math.round(b / 1024) + " KB" : b + " B";

function icoFor(mime, name) {
  const ext = (name.split(".").pop() || "").toUpperCase().slice(0, 4);
  const cls = mime === "application/pdf" ? "pdf" : mime.startsWith("image/") ? "img" : mime.startsWith("text/") ? "txt"
    : /word|sheet|excel|presentation|officedocument|opendocument/.test(mime) ? "office" : "";
  return `<span class="ico ${cls}">${esc(ext || "?")}</span>`;
}

// ---------- Dialog ----------
function closeSheet() { $("sheet").classList.add("hidden"); $("sheet").innerHTML = ""; }
$("sheet").addEventListener("mousedown", (e) => { if (e.target === $("sheet")) closeSheet(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("sheet").classList.contains("hidden")) closeSheet(); });
function sheet(html, cls = "") {
  $("sheet").innerHTML = `<div class="card ${cls}">${html}</div>`;
  $("sheet").classList.remove("hidden");
  const card = $("sheet").firstElementChild;
  $$("[data-x]", card).forEach(b => b.onclick = closeSheet);
  return card;
}
function busy(card, fn) {
  return async () => {
    const err = card.querySelector("[data-err]"); if (err) err.textContent = "";
    $$("button", card).forEach(b => { b.disabled = true; });
    try { await fn(); } catch (e) { if (err) err.textContent = e.message; else alert(e.message); }
    finally { $$("button", card).forEach(b => { b.disabled = false; }); }
  };
}
function swatchHtml(list, current, key) {
  return `<div class="swatches" data-sw="${key}">${list.map(c => `<button type="button" data-c="${c}" title="${c || "ohne"}"
    class="${c === current ? "on" : ""}" style="background:${c || "transparent"};${c ? "" : "border-color:var(--line)"}">${c ? "" : "–"}</button>`).join("")}</div>`;
}
function bindSwatches(card, key) {
  const box = card.querySelector(`[data-sw="${key}"]`);
  let val = (box.querySelector(".on") || {}).dataset ? (box.querySelector(".on") || { dataset: {} }).dataset.c : "";
  $$("button", box).forEach(b => b.onclick = () => { $$("button", box).forEach(x => x.classList.remove("on")); b.classList.add("on"); val = b.dataset.c; });
  return () => val;
}

// ---------- Einstieg ----------
async function docShow(tab, sub) {
  if (tab !== "regal") return;
  $("regalErr").textContent = "";
  try {
    await loadShelves();
    loadTags().catch(() => {});
    const m = /^([sb])(\d+)$/.exec(sub || "");
    if (m && m[1] === "b") return openBinder(Number(m[2]));
    const sid = m && m[1] === "s" ? Number(m[2]) : (R.shelfId && R.shelves.some(s => s.id === R.shelfId) ? R.shelfId : (R.shelves[0] || {}).id);
    if (sid) return openShelf(sid); else renderNoShelf();
  } catch (e) { $("regalErr").textContent = e.message; }
}
window.docShow = docShow;
function go(sub) { history.replaceState(null, "", "#regal/" + sub); state.sub = sub; }

async function loadShelves() {
  const r = await api("shelves");
  R.shelves = r.shelves; R.canCreate = r.can_create; R.hasShares = r.has_shares;
  renderShelfList();
}
function renderShelfList() {
  const activeShelf = R.binder ? R.binder.shelf.id : R.shelfId;
  $("shelfList").innerHTML = R.shelves.length ? R.shelves.map(s => `<button class="shelfItem ${s.id === activeShelf ? "on" : ""}" data-sid="${s.id}">
      ${esc(s.name)} <span>${s.binders} Ordner${s.right === "read" ? " · 👁" : ""}</span></button>`).join("")
    : '<p class="muted small" style="margin:4px 9px">Noch kein Regal.</p>';
  $$("#shelfList [data-sid]").forEach(b => b.onclick = () => openShelf(Number(b.dataset.sid)));
  $("shelfNew").classList.toggle("hidden", !R.canCreate);
}
async function loadTags() {
  R.tags = await api("tags");
  $("tagCloud").innerHTML = R.tags.filter(t => t.docs).length ? R.tags.filter(t => t.docs).map(t =>
    `<button data-tag="${t.id}" class="${R.tagFilter === t.id ? "on" : ""}">${esc(t.name)} <span class="muted">${t.docs}</span></button>`).join("")
    : '<span class="muted small">Noch keine.</span>';
  $$("#tagCloud [data-tag]").forEach(b => b.onclick = () => showTagResults(Number(b.dataset.tag)));
}

function renderNoShelf() {
  R.shelf = null; R.binder = null;
  $("regalMain").innerHTML = `<section class="card empty">
    <div style="font-size:42px">🗄️</div><h2>Noch kein Regal</h2>
    ${isAdmin() ? `<p>${R.hasShares ? "Lege dein erstes Regal an – z. B. „Privat“ oder „Praxis“." :
      "Schalte zuerst unter <b>Verwaltung → Freigaben</b> die freigegebenen Ordner ein, aus denen Dokumente ins Regal dürfen."}</p>
      <div class="row" style="justify-content:center">${R.hasShares ? '<button class="primary" data-new>＋ Regal anlegen</button>' : '<button class="primary" data-sh>Zu den Freigaben</button>'}</div>`
      : "<p>Für dich ist noch kein Regal freigegeben. Bitte beim Administrator melden.</p>"}</section>`;
  const n = $("regalMain").querySelector("[data-new]"); if (n) n.onclick = newShelf;
  const sh = $("regalMain").querySelector("[data-sh]"); if (sh) sh.onclick = () => { store.set("asec", "freigaben"); showTab("admin"); };
}

// ---------- Regalansicht ----------
async function openShelf(id) {
  R.shelf = await api(`shelves/${id}`); R.shelfId = id; R.binder = null; R.tagFilter = null;
  go("s" + id); renderShelfList(); renderShelf();
}
function spineHtml(x, edit) {
  const n = x.docs + (x.hidden ? ` (+${x.hidden})` : "");
  return `<button class="spine w${x.width}" data-bid="${x.id}" ${edit ? 'draggable="true"' : ""} style="background:${esc(x.color)}"
     title="${esc(x.title)} – ${x.docs} Dokumente${x.hidden ? `, ${x.hidden} ohne Berechtigung ausgeblendet` : ""}${x.missing ? `, ${x.missing} Datei(en) fehlen` : ""}">
     <span class="lb"><span>${esc(x.title)}</span></span><span class="ring"></span><span class="n">${x.width > 1 ? n + " Dok." : x.docs}</span>
     ${x.missing ? '<span class="dot"></span>' : ""}</button>`;
}
function renderShelf() {
  const s = R.shelf, edit = s.right === "edit";
  $("regalMain").innerHTML = `
    <div class="rtitle"><h2>Regal „${esc(s.name)}“ ${edit ? "" : '<span class="tag">nur ansehen</span>'}</h2>
      <div class="row">${isAdmin() ? '<button data-acc title="Wer darf dieses Regal sehen oder bearbeiten?">👥 Rechte</button>' : ""}
        ${edit ? '<button data-ren>Umbenennen</button><button data-board>＋ Fach</button><button class="primary" data-binder>＋ Ordner</button>' : ""}
        ${isAdmin() ? '<button class="danger" data-del title="Regal löschen">🗑</button>' : ""}</div></div>
    <div class="shelf">${s.boards.map(b => `<div class="board" data-board-id="${b.id}">
        <span class="blabel">${esc(b.name || "Fach")}${edit ? ` <button data-bren="${b.id}" title="Fach umbenennen">✎</button>${b.binders.length ? "" : `<button data-bdel="${b.id}" title="Leeres Fach entfernen">✕</button>`}` : ""}</span>
        ${b.binders.map(x => spineHtml(x, edit)).join("") || `<span class="bempty">${edit ? "Leeres Fach – Ordner hierher ziehen oder „＋ Ordner“" : "leer"}</span>`}
      </div>`).join("")}<div class="shelfEnd"></div></div>
    <p class="shelfHint">${edit ? "Ordner per Ziehen umstellen (auch in ein anderes Fach) · " : ""}Klick öffnet den Ordner ·
      Zahl = Dokumente, (+n) = ohne Berechtigung in DSM ausgeblendet · roter Punkt = Datei fehlt · Die Dateien bleiben in der File Station, das Regal verweist nur darauf.</p>`;
  const m = $("regalMain");
  $$(".spine", m).forEach(el => el.onclick = () => openBinder(Number(el.dataset.bid)));
  const on = (sel, fn) => { const el = m.querySelector(sel); if (el) el.onclick = fn; };
  on("[data-binder]", () => binderForm(null));
  on("[data-board]", async () => { const name = prompt("Name des neuen Fachs:", "Fach " + (s.boards.length + 1)); if (name === null) return;
    R.shelf = await api(`shelves/${s.id}/boards`, { method: "POST", body: { name } }); renderShelf(); });
  on("[data-ren]", async () => { const name = prompt("Neuer Name des Regals:", s.name); if (!name) return;
    R.shelf = await api(`shelves/${s.id}`, { method: "PATCH", body: { name } }); await loadShelves(); renderShelf(); });
  on("[data-acc]", () => accessForm(s.id));
  on("[data-del]", async () => {
    if (!confirm(`Regal „${s.name}“ mit allen Ordnern löschen?\n\nDie Dateien in den Freigaben bleiben unverändert – nur die Ablage im Regal geht verloren.`)) return;
    try { await api(`shelves/${s.id}`, { method: "DELETE" }); R.shelfId = null; await docShow("regal", ""); } catch (e) { $("regalErr").textContent = e.message; }
  });
  $$("[data-bren]", m).forEach(b => b.onclick = async () => {
    const board = s.boards.find(x => x.id === Number(b.dataset.bren));
    const name = prompt("Name des Fachs:", board.name); if (name === null) return;
    R.shelf = await api(`boards/${board.id}`, { method: "PATCH", body: { name } }); renderShelf();
  });
  $$("[data-bdel]", m).forEach(b => b.onclick = async () => {
    try { R.shelf = await api(`boards/${b.dataset.bdel}`, { method: "DELETE" }); renderShelf(); } catch (e) { $("regalErr").textContent = e.message; }
  });
  if (edit) bindShelfDnD(m);
}

function bindShelfDnD(m) {
  let dragged = null;
  $$(".spine", m).forEach(el => {
    el.addEventListener("dragstart", (e) => { dragged = el; el.classList.add("dragging"); e.dataTransfer.effectAllowed = "move";
      try { e.dataTransfer.setData("text/plain", el.dataset.bid); } catch (err) { /* egal */ } });
    el.addEventListener("dragend", () => { el.classList.remove("dragging"); $$(".board", m).forEach(b => b.classList.remove("dropping")); });
  });
  $$(".board", m).forEach(board => {
    board.addEventListener("dragover", (e) => {
      if (!dragged) return;
      e.preventDefault(); board.classList.add("dropping");
      const after = [...board.querySelectorAll(".spine:not(.dragging)")].find(s => e.clientX < s.getBoundingClientRect().left + s.offsetWidth / 2);
      const empty = board.querySelector(".bempty"); if (empty) empty.remove();
      if (after) board.insertBefore(dragged, after); else board.appendChild(dragged);
    });
    board.addEventListener("dragleave", () => board.classList.remove("dropping"));
    board.addEventListener("drop", async (e) => {
      e.preventDefault(); board.classList.remove("dropping");
      if (!dragged) return; dragged = null;
      const layout = { boards: $$(".board", m).map(b => ({ id: Number(b.dataset.boardId), binders: $$(".spine", b).map(s => Number(s.dataset.bid)) })) };
      try { R.shelf = await api(`shelves/${R.shelf.id}/layout`, { method: "PUT", body: layout }); }
      catch (err) { $("regalErr").textContent = err.message; R.shelf = await api(`shelves/${R.shelf.id}`); }
      renderShelf();
    });
  });
}

async function newShelf() {
  const card = sheet(`<div class="toolbar"><h2>Neues Regal</h2><button data-x>✕</button></div>
    <div class="formgrid"><label class="span2">Name <input data-k="name" maxlength="80" placeholder="z. B. Privat, Praxis, Haus &amp; Grundstück"></label>
    <label class="inline span2"><input type="checkbox" data-k="all_read"> alle Benutzer dürfen hineinsehen</label></div>
    <p class="muted small" style="margin:0">Das Regal bekommt zwei Fächer. Wer es sehen oder bearbeiten darf, stellst du danach unter „👥 Rechte“ ein.</p>
    <div class="row"><button class="primary" data-save>Anlegen</button><button data-x>Abbrechen</button></div><div class="err" data-err></div>`, "narrow");
  card.querySelector("[data-k=name]").focus();
  card.querySelector("[data-save]").onclick = busy(card, async () => {
    const s = await api("shelves", { method: "POST", body: { name: card.querySelector("[data-k=name]").value.trim(), all_read: card.querySelector("[data-k=all_read]").checked } });
    closeSheet(); await loadShelves(); await openShelf(s.id);
  });
}
$("shelfNew").onclick = newShelf;

async function accessForm(shelfId) {
  const a = await api(`shelves/${shelfId}/access`);
  const card = sheet(`<div class="toolbar"><h2>Rechte am Regal</h2><button data-x>✕</button></div>
    <label class="inline"><input type="checkbox" data-all ${a.all_read ? "checked" : ""}> alle Benutzer dürfen hineinsehen</label>
    <div class="scroll"><table><thead><tr><th>Benutzer</th><th>Recht</th></tr></thead><tbody>${a.users.map(u => `<tr><td>${esc(u.display_name)} <span class="muted small">${esc(u.username)}</span></td>
      <td>${u.is_admin ? '<span class="tag boss">Vollzugriff</span>' : `<select data-uid="${u.id}">
        <option value="">${a.all_read ? "ansehen (alle)" : "kein Zugriff"}</option><option value="read" ${u.right === "read" ? "selected" : ""}>ansehen</option>
        <option value="edit" ${u.right === "edit" ? "selected" : ""}>bearbeiten</option></select>`}</td></tr>`).join("")}</tbody></table></div>
    <p class="muted small" style="margin:0"><b>Ansehen</b>: Ordner öffnen, Dokumente anzeigen. <b>Bearbeiten</b>: zusätzlich Ordner anlegen, umstellen und Dokumente ablegen.
      Welche Dokumente jemand öffnen darf, entscheidet zusätzlich DSM: Dokumente aus Freigaben ohne Leserecht bleiben ausgeblendet.</p>
    <div class="row"><button class="primary" data-save>Speichern</button><button data-x>Abbrechen</button></div><div class="err" data-err></div>`);
  card.querySelector("[data-save]").onclick = busy(card, async () => {
    const users = $$("[data-uid]", card).filter(s => s.value).map(s => ({ user_id: Number(s.dataset.uid), can_edit: s.value === "edit" }));
    await api(`shelves/${shelfId}/access`, { method: "PUT", body: { all_read: card.querySelector("[data-all]").checked, users } });
    closeSheet(); await loadShelves();
  });
}

function binderForm(x) {
  const s = R.shelf || { boards: [] };
  const boards = x ? null : s.boards;
  const card = sheet(`<div class="toolbar"><h2>${x ? "Ordner bearbeiten" : "Neuer Ordner"}</h2><button data-x>✕</button></div>
    <div class="formgrid">
      <label class="span2">Rückenschild <input data-k="title" maxlength="60" value="${esc(x ? x.title : "")}" placeholder="z. B. Versicherungen, Steuer 2025"></label>
      ${boards ? `<label>Fach <select data-k="board">${boards.map(b => `<option value="${b.id}">${esc(b.name || "Fach")}</option>`).join("")}</select></label>` : ""}
      <label>Breite <select data-k="width"><option value="1">schmal</option><option value="2">normal</option><option value="3">breit</option></select></label>
      <label class="span2">Farbe ${swatchHtml(COLORS, x ? x.color : COLORS[5], "c")}</label>
      ${x ? "" : '<label class="span2">Register (eins pro Zeile, leer = „Allgemein“) <textarea data-k="tabs" rows="3" placeholder="Kfz&#10;Hausrat&#10;Haftpflicht"></textarea></label>'}
      <label class="span2">Notiz <textarea data-k="notes" rows="2" maxlength="2000">${esc(x ? x.notes : "")}</textarea></label>
    </div>
    <div class="row"><button class="primary" data-save>${x ? "Speichern" : "Anlegen"}</button><button data-x>Abbrechen</button>
      ${x ? '<button class="danger" data-del style="margin-left:auto">Ordner löschen</button>' : ""}</div><div class="err" data-err></div>`, "narrow");
  card.querySelector("[data-k=width]").value = String(x ? x.width : 2);
  card.querySelector("[data-k=title]").focus();
  const color = bindSwatches(card, "c");
  const v = (k) => { const el = card.querySelector(`[data-k=${k}]`); return el ? el.value : ""; };
  card.querySelector("[data-save]").onclick = busy(card, async () => {
    const body = { title: v("title").trim(), color: color(), width: Number(v("width")), notes: v("notes") };
    if (x) { R.binder = await api(`binders/${x.id}`, { method: "PATCH", body }); closeSheet(); renderBinder(); return; }
    body.board_id = Number(v("board")); body.tabs = v("tabs").split("\n").map(t => t.trim()).filter(Boolean);
    const b = await api("binders", { method: "POST", body });
    closeSheet(); await loadShelves(); await openBinder(b.id);
  });
  const del = card.querySelector("[data-del]");
  if (del) del.onclick = busy(card, async () => {
    if (!confirm(`Ordner „${x.title}“ löschen?\n\nDie Dateien bleiben in den Freigaben. Dokumente, die nur in diesem Ordner lagen, verschwinden aus dem Regal.`)) return;
    R.shelf = await api(`binders/${x.id}`, { method: "DELETE" }); closeSheet(); await loadShelves(); await openShelf(R.shelf.id);
  });
}

// ---------- geöffneter Ordner ----------
async function openBinder(id, docId) {
  R.binder = await api(`binders/${id}`);
  R.shelfId = R.binder.shelf.id; R.tagFilter = null;
  if (!R.binder.tabs.some(t => t.id === R.tabId)) R.tabId = R.binder.tabs[0].id;
  if (docId) { const t = R.binder.tabs.find(t => t.docs.some(d => d.id === docId)); if (t) R.tabId = t.id; }
  R.docId = docId || null;
  go("b" + id); renderShelfList(); renderBinder();
}
function currentTab() { return R.binder.tabs.find(t => t.id === R.tabId) || R.binder.tabs[0]; }
function renderBinder() {
  const b = R.binder, edit = b.right === "edit", t = currentTab();
  if (!t.docs.some(d => d.id === R.docId)) R.docId = t.docs.length ? t.docs[0].id : null;
  $("regalMain").innerHTML = `
    <div class="rtitle"><h2><span class="ico" style="background:${esc(b.color)};width:14px;height:22px"></span>${esc(b.title)}</h2>
      <div class="row"><button data-back>← zum Regal</button>${edit ? '<button data-edit>Ordner bearbeiten</button><button class="primary" data-file>＋ Dokumente ablegen</button>' : ""}</div></div>
    <div class="binder">
      <div class="btabs">${b.tabs.map(x => `<button class="btab ${x.id === t.id ? "on" : ""}" data-tab="${x.id}" style="--tc:${esc(x.color || "#a0aec0")}">
          <span>${esc(x.name)}</span><i>${x.docs.length}${x.hidden ? "+" + x.hidden : ""}</i></button>`).join("")}
        ${edit ? '<button class="linkbtn" data-newtab style="text-align:left;margin-left:12px">＋ Register</button>' : ""}</div>
      <div class="blist">
        <div class="crumb"><a data-shelf>Regal ${esc(b.shelf.name)}</a> › <a data-back2>${esc(b.title)}</a> › <b>${esc(t.name)}</b>
          ${edit ? `<button class="mini" data-tedit style="margin-left:6px">Register bearbeiten</button>` : ""}</div>
        ${b.notes ? `<p class="bnote">${esc(b.notes)}</p>` : ""}
        ${t.docs.length ? `<div class="scroll"><table class="docs"><thead><tr><th>Dokument</th><th>Datum</th><th>Schlagworte</th></tr></thead><tbody>${t.docs.map(d => `
          <tr data-doc="${d.id}" class="${d.id === R.docId ? "sel" : ""} ${d.missing ? "missing" : ""}" ${edit ? 'draggable="true"' : ""}>
            <td><div style="display:flex">${icoFor(d.mime, d.filename)}<div><b>${esc(d.title)}</b>${d.missing ? ' <span class="tag warn" title="Datei verschoben oder gelöscht">fehlt</span>' : ""}
              <div class="dpath">${esc(d.share)}/${esc(d.path)}</div></div></div></td>
            <td class="nowrap">${deDate(d.doc_date) || '<span class="muted">–</span>'}</td>
            <td>${d.tags.map(x => `<span class="dtag">${esc(x.name)}</span>`).join("")}</td></tr>`).join("")}</tbody></table></div>`
          : `<div class="empty">${edit ? "Noch leer. Mit <b>＋ Dokumente ablegen</b> Dateien aus deinen Freigaben hineinlegen." : "Keine Dokumente in diesem Register."}</div>`}
        ${t.hidden ? `<p class="muted small">${t.hidden} weitere Dokument(e) liegen in Freigaben, die du in DSM nicht lesen darfst.</p>` : ""}
      </div>
      <div class="bprev" id="bprev"></div>
    </div>`;
  const m = $("regalMain");
  const on = (sel, fn) => { const el = m.querySelector(sel); if (el) el.onclick = fn; };
  on("[data-back]", () => openShelf(b.shelf.id)); on("[data-back2]", () => openShelf(b.shelf.id)); on("[data-shelf]", () => openShelf(b.shelf.id));
  on("[data-edit]", () => binderForm(b));
  on("[data-file]", () => filePicker(t.id));
  on("[data-newtab]", () => tabForm(null));
  on("[data-tedit]", () => tabForm(t));
  $$("[data-tab]", m).forEach(el => el.onclick = () => { R.tabId = Number(el.dataset.tab); R.docId = null; renderBinder(); });
  $$("[data-doc]", m).forEach(el => el.onclick = () => { R.docId = Number(el.dataset.doc); $$("[data-doc]", m).forEach(r => r.classList.toggle("sel", r === el)); showDoc(); });
  if (edit) bindDocDnD(m);
  showDoc();
}

function bindDocDnD(m) {
  // Dokument auf ein anderes Register ziehen = verschieben (mit Strg/Alt: zusätzlich ablegen)
  $$("tr[data-doc]", m).forEach(tr => tr.addEventListener("dragstart", (e) => {
    e.dataTransfer.setData("text/plain", "doc:" + tr.dataset.doc); e.dataTransfer.effectAllowed = "copyMove"; }));
  $$(".btab[data-tab]", m).forEach(tab => {
    tab.addEventListener("dragover", (e) => { e.preventDefault(); tab.classList.add("drop"); });
    tab.addEventListener("dragleave", () => tab.classList.remove("drop"));
    tab.addEventListener("drop", async (e) => {
      e.preventDefault(); tab.classList.remove("drop");
      const v = e.dataTransfer.getData("text/plain"); if (!v.startsWith("doc:")) return;
      const to = Number(tab.dataset.tab), from = R.tabId; if (to === from) return;
      try {
        await api(`docs/${v.slice(4)}/move`, { method: "POST", body: { from_tab: from, to_tab: to, keep: e.ctrlKey || e.altKey } });
        await openBinder(R.binder.id);
      } catch (err) { $("regalErr").textContent = err.message; }
    });
  });
}

function tabForm(t) {
  const card = sheet(`<div class="toolbar"><h2>${t ? "Register bearbeiten" : "Neues Register"}</h2><button data-x>✕</button></div>
    <div class="formgrid"><label class="span2">Name <input data-k="name" maxlength="40" value="${esc(t ? t.name : "")}"></label>
      <label class="span2">Farbe ${swatchHtml(TAB_COLORS, t ? t.color : "", "t")}</label></div>
    <div class="row"><button class="primary" data-save>Speichern</button><button data-x>Abbrechen</button>
      ${t ? '<button data-up>↑</button><button data-down>↓</button><button class="danger" data-del style="margin-left:auto">Löschen</button>' : ""}</div>
    <div class="err" data-err></div>`, "narrow");
  card.querySelector("[data-k=name]").focus();
  const color = bindSwatches(card, "t");
  card.querySelector("[data-save]").onclick = busy(card, async () => {
    const body = { name: card.querySelector("[data-k=name]").value.trim(), color: color() };
    R.binder = t ? await api(`tabs/${t.id}`, { method: "PATCH", body }) : await api(`binders/${R.binder.id}/tabs`, { method: "POST", body });
    if (!t) R.tabId = R.binder.tabs[R.binder.tabs.length - 1].id;
    closeSheet(); renderBinder();
  });
  if (!t) return;
  const move = (dir) => busy(card, async () => {
    const ids = R.binder.tabs.map(x => x.id), i = ids.indexOf(t.id), j = i + dir;
    if (j < 0 || j >= ids.length) return;
    [ids[i], ids[j]] = [ids[j], ids[i]];
    R.binder = await api(`binders/${R.binder.id}/tab-order`, { method: "PUT", body: { ids } }); closeSheet(); renderBinder();
  });
  card.querySelector("[data-up]").onclick = move(-1); card.querySelector("[data-down]").onclick = move(1);
  card.querySelector("[data-del]").onclick = busy(card, async () => {
    if (!confirm(`Register „${t.name}“ löschen?`)) return;
    R.binder = await api(`tabs/${t.id}`, { method: "DELETE" }); closeSheet(); renderBinder();
  });
}

// ---------- Vorschau & Details ----------
async function fetchBlob(path) {
  const res = await fetch(url(path), { credentials: "same-origin", cache: "no-store", headers: authHeaders() });
  if (!res.ok) { let msg = "Fehler " + res.status; try { const d = await res.json(); if (typeof d.detail === "string") msg = d.detail; } catch (e) { /* egal */ } throw new Error(msg); }
  return res.blob();
}
function setBlob(b) { if (R.blob) URL.revokeObjectURL(R.blob); R.blob = b ? URL.createObjectURL(b) : null; return R.blob; }
async function renderPreview(box, path, mime) {
  box.innerHTML = '<span class="muted small">Vorschau wird geladen …</span>';
  const viewable = mime === "application/pdf" || /^image\/(png|jpeg|gif|webp)$/.test(mime) || mime === "text/plain";
  if (!viewable) { box.innerHTML = '<span class="muted small" style="padding:12px;text-align:center">Für diese Dateiart gibt es keine Vorschau.<br>Mit „Herunterladen“ öffnen.</span>'; return; }
  try {
    const blob = await fetchBlob(path + (path.includes("?") ? "&" : "?") + "inline=1");
    if (mime === "text/plain") { const pre = document.createElement("pre"); pre.textContent = (await blob.text()).slice(0, 200000); box.innerHTML = ""; box.appendChild(pre); return; }
    const u = setBlob(blob);
    box.innerHTML = mime === "application/pdf" ? `<iframe src="${u}" title="Vorschau"></iframe>` : `<img src="${u}" alt="Vorschau">`;
  } catch (e) { box.innerHTML = `<span class="err small" style="padding:12px;text-align:center">${esc(e.message)}</span>`; }
}
async function showDoc() {
  const box = $("bprev"); if (!box) return;
  if (!R.docId) { setBlob(null); box.innerHTML = '<div class="empty small">Dokument auswählen, um es hier anzusehen.</div>'; return; }
  let d;
  try { d = await api(`docs/${R.docId}`); } catch (e) { box.innerHTML = `<div class="err">${esc(e.message)}</div>`; return; }
  box.innerHTML = `<div class="pview" data-pv></div>
    <div class="row"><button class="mini" data-open ${d.missing ? "disabled" : ""}>Öffnen</button><button class="mini" data-dl ${d.missing ? "disabled" : ""}>Herunterladen</button>
      ${d.can_edit ? '<button class="mini" data-edit>Bearbeiten</button><button class="mini" data-move>Verschieben …</button><button class="mini danger" data-rm title="Aus diesem Register nehmen – die Datei bleibt auf der NAS">Entfernen</button>' : ""}</div>
    <dl class="meta"><dt>Titel</dt><dd><b>${esc(d.title)}</b></dd><dt>Datum</dt><dd>${deDate(d.doc_date) || "–"}</dd>
      <dt>Datei</dt><dd>${esc(d.filename)}${d.missing ? ' <span class="tag warn">fehlt</span>' : ""}</dd><dt>Freigabe</dt><dd>/${esc(d.share)}/${esc(d.path.split("/").slice(0, -1).join("/"))}</dd>
      <dt>Größe</dt><dd>${fmtBytes(d.size)}</dd>
      ${d.tags.length ? `<dt>Schlagworte</dt><dd>${d.tags.map(x => `<span class="dtag">${esc(x.name)}</span>`).join("")}</dd>` : ""}
      ${d.notes ? `<dt>Notiz</dt><dd style="white-space:pre-wrap">${esc(d.notes)}</dd>` : ""}
      <dt>Liegt in</dt><dd class="places">${d.places.map(p => `<a data-go="${p.binder_id}">${esc(p.shelf)} › ${esc(p.binder)} › ${esc(p.tab)}</a>`).join("<br>")}</dd></dl>
    ${d.missing ? '<p class="small err" style="margin:0">Die Datei ist nicht mehr am gespeicherten Ort. Die nächtliche Prüfung sucht sie in den Freigaben (über ihren Fingerabdruck) – oder ein Admin startet sie unter Verwaltung → Freigaben.</p>' : ""}`;
  if (!d.missing) renderPreview(box.querySelector("[data-pv]"), `api/v1/docs/${d.id}/file`, d.mime);
  else box.querySelector("[data-pv]").innerHTML = '<span class="muted">Datei fehlt</span>';
  const on = (sel, fn) => { const el = box.querySelector(sel); if (el) el.onclick = fn; };
  on("[data-dl]", () => downloadFile(`api/v1/docs/${d.id}/file`, d.filename).catch(e => alert(e.message)));
  on("[data-open]", async () => {
    const w = window.open("", "_blank");
    try { const u = URL.createObjectURL(await fetchBlob(`api/v1/docs/${d.id}/file?inline=1`)); if (w) w.location = u; else location.href = u; }
    catch (e) { if (w) w.close(); alert(e.message); }
  });
  on("[data-edit]", () => docForm(d));
  on("[data-move]", () => moveForm(d));
  on("[data-rm]", async () => {
    const only = d.places.length <= 1;
    if (!confirm(`„${d.title}“ aus dem Register „${currentTab().name}“ nehmen?` + (only ? "\n\nEs liegt sonst nirgends – es verschwindet aus dem Regal." : "") +
                 "\n\nDie Datei auf der NAS bleibt unverändert.")) return;
    try { R.binder = await api(`tabs/${R.tabId}/docs/${d.id}`, { method: "DELETE" }); R.docId = null; renderBinder(); loadTags(); }
    catch (e) { $("regalErr").textContent = e.message; }
  });
  $$("[data-go]", box).forEach(a => a.onclick = () => openBinder(Number(a.dataset.go), d.id));
}

function tagInputHtml(tags) {
  return `<input data-k="tags" value="${esc(tags.join(", "))}" placeholder="durch Komma getrennt, z. B. Kfz, HUK" list="dl_tags">
    <datalist id="dl_tags">${R.tags.map(t => `<option value="${esc(t.name)}">`).join("")}</datalist>`;
}
const splitTags = (s) => s.split(",").map(x => x.trim()).filter(Boolean);
function docForm(d) {
  const card = sheet(`<div class="toolbar"><h2>Dokument bearbeiten</h2><button data-x>✕</button></div>
    <div class="formgrid"><label class="span2">Titel <input data-k="title" maxlength="200" value="${esc(d.title)}"></label>
      <label>Datum <input data-k="date" type="date" value="${esc(d.doc_date || "")}"></label><label>Schlagworte ${tagInputHtml(d.tags.map(t => t.name))}</label>
      <label class="span2">Notiz <textarea data-k="notes" rows="3" maxlength="5000">${esc(d.notes)}</textarea></label></div>
    <p class="muted small" style="margin:0">Datei: /${esc(d.share)}/${esc(d.path)} – Titel und Notiz ändern nur die Ablage, nicht die Datei.</p>
    <div class="row"><button class="primary" data-save>Speichern</button><button data-x>Abbrechen</button></div><div class="err" data-err></div>`, "narrow");
  const v = (k) => card.querySelector(`[data-k=${k}]`).value;
  card.querySelector("[data-save]").onclick = busy(card, async () => {
    await api(`docs/${d.id}`, { method: "PATCH", body: { title: v("title").trim(), doc_date: v("date") || null, notes: v("notes"), tags: splitTags(v("tags")) } });
    closeSheet(); await openBinder(R.binder.id, d.id); loadTags();
  });
}

async function moveForm(d) {
  const card = sheet(`<div class="toolbar"><h2>Verschieben oder zusätzlich ablegen</h2><button data-x>✕</button></div>
    <div class="formgrid"><label>Regal <select data-k="shelf">${R.shelves.filter(s => s.right === "edit").map(s => `<option value="${s.id}">${esc(s.name)}</option>`).join("")}</select></label>
      <label>Ordner <select data-k="binder"></select></label><label class="span2">Register <select data-k="tab"></select></label>
      <label class="inline span2"><input type="checkbox" data-k="keep"> zusätzlich ablegen (bleibt auch hier liegen)</label></div>
    <div class="row"><button class="primary" data-save>Übernehmen</button><button data-x>Abbrechen</button></div><div class="err" data-err></div>`, "narrow");
  const sel = (k) => card.querySelector(`[data-k=${k}]`);
  const fillTabs = async () => { const b = await api(`binders/${sel("binder").value}`); sel("tab").innerHTML = b.tabs.map(t => `<option value="${t.id}">${esc(t.name)}</option>`).join(""); };
  const fillBinders = async () => {
    const s = await api(`shelves/${sel("shelf").value}`);
    const all = s.boards.flatMap(b => b.binders);
    sel("binder").innerHTML = all.map(x => `<option value="${x.id}">${esc(x.title)}</option>`).join("");
    if (all.some(x => x.id === R.binder.id)) sel("binder").value = String(R.binder.id);
    if (all.length) await fillTabs(); else sel("tab").innerHTML = "";
  };
  sel("shelf").value = String(R.binder.shelf.id);
  sel("shelf").onchange = () => fillBinders().catch(e => { card.querySelector("[data-err]").textContent = e.message; });
  sel("binder").onchange = () => fillTabs().catch(e => { card.querySelector("[data-err]").textContent = e.message; });
  await fillBinders();
  card.querySelector("[data-save]").onclick = busy(card, async () => {
    if (!sel("tab").value) throw new Error("Bitte ein Register wählen");
    await api(`docs/${d.id}/move`, { method: "POST", body: { from_tab: R.tabId, to_tab: Number(sel("tab").value), keep: sel("keep").checked } });
    closeSheet(); await openBinder(R.binder.id);
  });
}

// ---------- Dokumente ablegen (Datei-Auswahl aus den Freigaben) ----------
async function filePicker(tabId) {
  const my = await api("shares");
  if (!my.length) {
    sheet(`<div class="toolbar"><h2>Dokumente ablegen</h2><button data-x>✕</button></div>
      <p>Du hast noch keinen Zugriff auf freigegebene Ordner im Regal.</p>
      <p class="muted small">${isAdmin() ? "Unter <b>Verwaltung → Freigaben</b> die Ordner einschalten, aus denen Dokumente ins Regal dürfen." :
        "Ein Administrator muss die Freigabe im Regal einschalten – und du brauchst in DSM Leserechte darauf."}</p>
      <div class="row"><button data-x>Schließen</button></div>`, "narrow");
    return;
  }
  const last = store.get("pick") ? JSON.parse(store.get("pick")) : {};
  const st = { share: my.some(s => s.name === last.share) ? last.share : my[0].name, path: my.some(s => s.name === last.share) ? (last.path || "") : "", chosen: new Map(), filter: "" };
  const t = currentTab();
  const card = sheet(`<div class="toolbar"><h2>Dokumente ablegen in „${esc(R.binder.title)} › ${esc(t.name)}“</h2><button data-x>✕</button></div>
    <div class="picker">
      <div class="row"><label>Freigabe <select data-share>${my.map(s => `<option>${esc(s.name)}</option>`).join("")}</select></label>
        <label style="flex:1">Filtern <input data-filter type="search" placeholder="Dateiname …"></label></div>
      <div class="crumb" data-crumb></div>
      <div class="flist" data-list></div>
      <div class="formgrid"><label>Datum (optional) <input data-k="date" type="date"></label><label>Schlagworte (optional) ${tagInputHtml([])}</label></div>
      <p class="muted small" style="margin:0">Die Dateien bleiben, wo sie sind – das Regal merkt sich nur den Ort und erkennt sie über ihren Fingerabdruck wieder, wenn sie verschoben werden.</p>
      <div class="row"><button class="primary" data-save disabled>Ablegen</button><button data-x>Abbrechen</button><span class="muted small" data-count></span></div>
      <div class="err" data-err></div></div>`, "wide");
  const q = (s) => card.querySelector(s);
  q("[data-share]").value = st.share;
  const updCount = () => { const n = st.chosen.size; q("[data-save]").disabled = !n; q("[data-save]").textContent = n ? `${n} ablegen` : "Ablegen"; q("[data-count]").textContent = n ? `${n} ausgewählt` : ""; };
  const load = async () => {
    q("[data-list]").innerHTML = '<div class="frow muted">Wird geladen …</div>';
    let r;
    try { r = await api(`browse?share=${encodeURIComponent(st.share)}&path=${encodeURIComponent(st.path)}`); }
    catch (e) { if (st.path) { st.path = ""; return load(); } q("[data-list]").innerHTML = `<div class="frow err">${esc(e.message)}</div>`; return; }
    store.set("pick", JSON.stringify({ share: st.share, path: st.path }));
    const parts = st.path ? st.path.split("/") : [];
    q("[data-crumb]").innerHTML = `<a data-p="">/${esc(st.share)}</a>` + parts.map((p, i) => ` › <a data-p="${esc(parts.slice(0, i + 1).join("/"))}">${esc(p)}</a>`).join("");
    $$("[data-p]", q("[data-crumb]")).forEach(a => a.onclick = () => { st.path = a.dataset.p; load(); });
    const f = st.filter.toLowerCase();
    const dirs = r.dirs.filter(d => !f || d.name.toLowerCase().includes(f)), files = r.files.filter(x => !f || x.name.toLowerCase().includes(f));
    q("[data-list]").innerHTML = (st.path ? `<div class="frow dir" data-dir="${esc(parts.slice(0, -1).join("/"))}">⬆️ <span class="fname">eine Ebene höher</span></div>` : "") +
      dirs.map(d => `<div class="frow dir" data-dir="${esc(d.path)}">📁 <span class="fname">${esc(d.name)}</span></div>`).join("") +
      files.map(x => `<label class="frow"><input type="checkbox" data-file="${esc(x.path)}" ${st.chosen.has(st.share + "/" + x.path) ? "checked" : ""}>
        ${icoFor(x.mime, x.name)}<span class="fname">${esc(x.name)}${x.doc_id ? ' <span class="tag" title="liegt schon im Regal">im Regal</span>' : ""}</span>
        <span class="fsize">${fmtBytes(x.size)} · ${new Date(x.mtime * 1000).toLocaleDateString("de-DE")}</span>
        <button type="button" class="mini" data-peek="${esc(x.path)}" data-mime="${esc(x.mime)}">Ansehen</button></label>`).join("") +
      (!dirs.length && !files.length ? '<div class="frow muted">Hier liegt nichts.</div>' : "");
    $$("[data-dir]", q("[data-list]")).forEach(el => el.onclick = () => { st.path = el.dataset.dir; load(); });
    $$("[data-file]", q("[data-list]")).forEach(cb => cb.onchange = () => {
      const k = st.share + "/" + cb.dataset.file;
      if (cb.checked) st.chosen.set(k, { share: st.share, path: cb.dataset.file }); else st.chosen.delete(k);
      updCount();
    });
    $$("[data-peek]", q("[data-list]")).forEach(b => b.onclick = (e) => { e.preventDefault(); peek(st.share, b.dataset.peek, b.dataset.mime); });
  };
  q("[data-share]").onchange = () => { st.share = q("[data-share]").value; st.path = ""; load(); };
  let timer; q("[data-filter]").oninput = () => { clearTimeout(timer); timer = setTimeout(() => { st.filter = q("[data-filter]").value.trim(); load(); }, 200); };
  q("[data-save]").onclick = busy(card, async () => {
    const r = await api(`tabs/${tabId}/docs`, { method: "POST", body: { files: [...st.chosen.values()], doc_date: q("[data-k=date]").value || null,
                                                                       tags: splitTags(q("[data-k=tags]").value) } });
    closeSheet(); R.binder = r.binder; R.docId = null; renderBinder(); loadTags(); loadShelves();
    if (r.already) $("regalErr").innerHTML = `<span class="muted">${r.already} Datei(en) lagen schon in diesem Register.</span>`;
  });
  await load(); updCount();
}
function peek(share, path, mime) {
  const box = document.createElement("div");
  box.className = "sheet"; box.style.zIndex = 60;
  box.innerHTML = `<div class="card wide"><div class="toolbar"><h2>${esc(path.split("/").pop())}</h2><button data-x>✕</button></div><div class="pview" style="height:70vh"></div></div>`;
  document.body.appendChild(box);
  const close = () => box.remove();
  box.querySelector("[data-x]").onclick = close; box.addEventListener("mousedown", (e) => { if (e.target === box) close(); });
  renderPreview(box.querySelector(".pview"), `api/v1/browse/file?share=${encodeURIComponent(share)}&path=${encodeURIComponent(path)}`, mime);
}

// ---------- Suche ----------
let searchTimer = null;
$("gq").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(runSearch, 220); });
$("gq").addEventListener("keydown", (e) => { if (e.key === "Escape") { $("gq").value = ""; $("gres").classList.add("hidden"); } });
document.addEventListener("keydown", (e) => {
  if (e.key === "/" && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName) && $("sheet").classList.contains("hidden")) { e.preventDefault(); $("gq").focus(); }
});
document.addEventListener("click", (e) => { if (!e.target.closest(".hsearch")) $("gres").classList.add("hidden"); });
async function runSearch() {
  const q = $("gq").value.trim();
  if (q.length < 2) { $("gres").classList.add("hidden"); return; }
  try {
    const r = await api(`search?q=${encodeURIComponent(q)}&limit=20`);
    $("gres").innerHTML = r.results.length ? r.results.map(hitHtml).join("") : '<div class="muted small" style="padding:10px">Nichts gefunden.</div>';
    $("gres").classList.remove("hidden");
    bindHits($("gres"));
  } catch (e) { /* egal */ }
}
function hitHtml(d) {
  const p = d.places[0];
  return `<button class="hit" data-hb="${p.binder_id}" data-hd="${d.id}">${icoFor(d.mime, d.filename)}<b>${esc(d.title)}</b>
    <small>${esc(p.shelf)} › ${esc(p.binder)} › ${esc(p.tab)}${d.places.length > 1 ? ` (+${d.places.length - 1})` : ""} · ${d.tags.map(t => esc(t.name)).join(", ")}</small></button>`;
}
function bindHits(root) {
  $$("[data-hb]", root).forEach(b => b.onclick = () => {
    $("gres").classList.add("hidden");
    if (state.tab !== "regal") showTab("regal", "b" + b.dataset.hb).then(() => openBinder(Number(b.dataset.hb), Number(b.dataset.hd)));
    else openBinder(Number(b.dataset.hb), Number(b.dataset.hd));
  });
}
async function showTagResults(tagId) {
  R.tagFilter = R.tagFilter === tagId ? null : tagId;
  await loadTags();
  if (!R.tagFilter) { if (R.shelfId) return openShelf(R.shelfId); return; }
  const tag = R.tags.find(t => t.id === tagId);
  const r = await api(`search?tag=${tagId}&limit=200`);
  R.binder = null;
  $("regalMain").innerHTML = `<div class="rtitle"><h2>Schlagwort „${esc(tag ? tag.name : "")}“</h2><button data-close>✕ Filter aufheben</button></div>
    <section class="card sres" style="position:static;width:auto">${r.results.map(hitHtml).join("") || '<p class="muted">Keine Dokumente.</p>'}</section>`;
  $("regalMain").querySelector("[data-close]").onclick = () => showTagResults(tagId);
  bindHits($("regalMain"));
}

// ---------- Verwaltung: Freigaben ----------
async function loadSharesAdmin(data) {
  const r = data || await api("admin/shares");
  $("shTable").innerHTML = r.shares.length ? `<thead><tr><th>Im Regal</th><th>Freigabe</th><th>Pfad</th><th>Zugriff des Regals</th><th class="num">Dokumente</th></tr></thead><tbody>` +
    r.shares.map(s => `<tr><td><input type="checkbox" data-sh="${esc(s.name)}" ${s.enabled ? "checked" : ""} ${!s.readable && !s.enabled ? "disabled" : ""}></td>
      <td><b>${esc(s.name)}</b></td><td class="mono small">${esc(s.path)}</td>
      <td>${!s.exists ? '<span class="tag off">nicht gefunden</span>' : s.writable ? '<span class="ok">lesen &amp; schreiben</span>'
        : s.readable ? "nur lesen" : '<span class="tag warn">kein Zugriff</span> <span class="muted small">siehe Anleitung unten</span>'}</td>
      <td class="num">${s.files}</td></tr>`).join("") + "</tbody>"
    : '<tbody><tr><td class="muted">Keine freigegebenen Ordner gefunden.</td></tr></tbody>';
  $("shInfo").innerHTML = r.missing_files ? `<span class="tag warn">${r.missing_files} Datei(en) fehlen</span> – verschoben, umbenannt oder gelöscht. „Dateien jetzt prüfen“ sucht sie in den eingeschalteten Freigaben.` : "";
  if (r.shares.some(s => !s.readable)) $("shHelp").open = true;
  $$("#shTable [data-sh]").forEach(cb => cb.onchange = async () => {
    $("shErr").textContent = "";
    try { await loadSharesAdmin(await api(`admin/shares/${encodeURIComponent(cb.dataset.sh)}`, { method: "PATCH", body: { enabled: cb.checked } })); }
    catch (e) { $("shErr").textContent = e.message; cb.checked = !cb.checked; }
  });
}
window.loadSharesAdmin = loadSharesAdmin;
$("shCheck").onclick = async () => {
  $("shErr").textContent = ""; $("shCheck").disabled = true;
  try {
    const r = await api("admin/filecheck", { method: "POST" });
    await loadSharesAdmin(r);
    okMsg($("shErr"), `Geprüft: ${r.ok} vorhanden, ${r.relinked} wiedergefunden, ${r.missing} fehlen.`);
  } catch (e) { $("shErr").textContent = e.message; }
  finally { $("shCheck").disabled = false; }
};

// Freigaben im Benutzer-Bearbeiten (eigene Konten)
async function userSharesBox(id, el) {
  try {
    const r = await api(`users/${id}/shares`);
    if (r.mode === "dsm") {
      el.innerHTML = `<p class="muted small" style="margin:0">📁 Freigaben kommen aus <b>DSM</b> (zuletzt übernommen ${fmtDT(r.dsm_synced_at)}):
        ${r.shares.filter(s => s.read).map(s => esc(s.name) + (s.write ? "" : " (lesen)")).join(", ") || "keine der eingeschalteten"}.</p>`;
      return;
    }
    if (r.mode === "admin") { el.innerHTML = '<p class="muted small" style="margin:0">📁 Vollzugriff mit eigenem Konto: alle eingeschalteten Freigaben.</p>'; return; }
    if (!r.shares.length) { el.innerHTML = '<p class="muted small" style="margin:0">📁 Noch keine Freigaben im Regal eingeschaltet.</p>'; return; }
    el.innerHTML = `<span class="small"><b>Freigaben</b> (eigenes Konto ohne DSM – hier festlegen):</span>
      <div class="row">${r.shares.map(s => `<label class="inline small"><input type="checkbox" data-us="${esc(s.name)}" ${s.read ? "checked" : ""}> ${esc(s.name)}</label>`).join("")}
      <button class="mini" data-uss>Freigaben speichern</button></div>`;
    el.querySelector("[data-uss]").onclick = async () => {
      const shares = {}; $$("[data-us]", el).filter(c => c.checked).forEach(c => { shares[c.dataset.us] = false; });
      try { await api(`users/${id}/shares`, { method: "PUT", body: { shares } }); okMsg($("uErr"), "Freigaben gespeichert."); } catch (e) { $("uErr").textContent = e.message; }
    };
  } catch (e) { el.innerHTML = `<span class="err small">${esc(e.message)}</span>`; }
}
window.userSharesBox = userSharesBox;
