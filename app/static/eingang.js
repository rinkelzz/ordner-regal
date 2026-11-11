"use strict";
/* global E */
// Ordner-Regal 0.2: Scanner-Eingang – neue Dateien ansehen und einsortieren, Zielordner wählen.
window.E = { items: [], sources: [], sel: 0, source: "", shelfId: null, shelf: null, binderId: null, binder: null, nameTouched: false, count: 0 };

// ---------- Zähler im Reiter ----------
async function inboxCount() {
  try {
    const r = await api("inbox/count");
    E.count = r.count;
    const b = $("inBadge");
    b.textContent = r.count > 99 ? "99+" : String(r.count);
    b.classList.toggle("hidden", !r.count);
    $$(".trayCount").forEach(el => { el.textContent = r.count ? `${r.count} neu` : "leer"; });
  } catch (e) { /* egal */ }
}
window.inboxCount = inboxCount;
setInterval(() => { if (state.me) inboxCount(); }, 60000);

// ---------- Ordner wählen (Zielordner) ----------
// Liefert {share, path}, "" (= kein Zielordner) oder null (abgebrochen).
function pickFolder({ title = "Zielordner wählen", start = null, allowNone = false, extra = "", writable = true, altLabel = "" } = {}) {
  return new Promise(async (resolve) => {
    let my;
    try { my = (await api("shares")).filter(s => s.can_write || !writable); } catch (e) { alert(e.message); resolve(null); return; }
    if (!my.length) { alert(writable ? "Du darfst in keiner Freigabe des Regals schreiben." : "Noch keine Freigabe im Regal eingeschaltet."); resolve(null); return; }
    const st = { share: start && my.some(s => s.name === start.share) ? start.share : my[0].name, path: start ? start.path || "" : "" };
    const box = document.createElement("div");
    box.className = "sheet"; box.style.zIndex = 60;
    box.innerHTML = `<div class="card narrow"><div class="toolbar"><h2>${esc(title)}</h2><button data-x>✕</button></div>
      ${extra}
      <label>Freigabe <select data-share>${my.map(s => `<option>${esc(s.name)}</option>`).join("")}</select></label>
      <div class="crumb" data-crumb></div><div class="flist" data-list style="max-height:40vh"></div>
      <div class="row"><button class="primary" data-ok>Diesen Ordner wählen</button>${writable ? "<button data-new>＋ Neuer Unterordner</button>" : ""}
        ${allowNone ? '<button data-none>Kein Zielordner</button>' : ""}${altLabel ? `<button data-alt>${esc(altLabel)}</button>` : ""}<button data-x>Abbrechen</button></div><div class="err" data-err></div></div>`;
    document.body.appendChild(box);
    const q = (s) => box.querySelector(s);
    const done = (v) => { box.remove(); resolve(v); };
    $$("[data-x]", box).forEach(b => b.onclick = () => done(null));
    box.addEventListener("mousedown", (e) => { if (e.target === box) done(null); });
    q("[data-share]").value = st.share;
    const load = async () => {
      q("[data-err]").textContent = "";
      let r;
      try { r = await api(`browse?share=${encodeURIComponent(st.share)}&path=${encodeURIComponent(st.path)}`); }
      catch (e) { if (st.path) { st.path = ""; return load(); } q("[data-err]").textContent = e.message; return; }
      const parts = st.path ? st.path.split("/") : [];
      q("[data-crumb]").innerHTML = `<a data-p="">/${esc(st.share)}</a>` + parts.map((p, i) => ` › <a data-p="${esc(parts.slice(0, i + 1).join("/"))}">${esc(p)}</a>`).join("");
      $$("[data-p]", q("[data-crumb]")).forEach(a => a.onclick = () => { st.path = a.dataset.p; load(); });
      q("[data-list]").innerHTML = (st.path ? `<div class="frow dir" data-dir="${esc(parts.slice(0, -1).join("/"))}">⬆️ <span class="fname">eine Ebene höher</span></div>` : "") +
        r.dirs.map(d => `<div class="frow dir" data-dir="${esc(d.path)}">📁 <span class="fname">${esc(d.name)}</span></div>`).join("") +
        (!r.dirs.length ? '<div class="frow muted">Keine Unterordner.</div>' : "");
      $$("[data-dir]", q("[data-list]")).forEach(el => el.onclick = () => { st.path = el.dataset.dir; load(); });
      q("[data-ok]").textContent = `„/${st.share}${st.path ? "/" + st.path : ""}“ wählen`;
    };
    q("[data-share]").onchange = () => { st.share = q("[data-share]").value; st.path = ""; load(); };
    q("[data-ok]").onclick = () => done({ share: st.share, path: st.path, remember: !!(q("[data-remember]") && q("[data-remember]").checked) });
    if (q("[data-none]")) q("[data-none]").onclick = () => done("");
    if (q("[data-alt]")) q("[data-alt]").onclick = () => done("alt");
    if (q("[data-new]")) q("[data-new]").onclick = async () => {
      const name = prompt("Name des neuen Ordners:"); if (!name) return;
      try { const r = await api("browse/mkdir", { method: "POST", body: { share: st.share, path: st.path, name } }); st.path = r.path; load(); }
      catch (e) { q("[data-err]").textContent = e.message; }
    };
    load();
  });
}
window.pickFolder = pickFolder;
const targetText = (t) => t ? `/${t.share}${t.path ? "/" + t.path : ""}` : "";
window.targetText = targetText;

// ---------- Seite „Eingang“ ----------
async function inboxShow() {
  const m = $("eingangMain");
  try {
    const r = await api("inbox" + (E.source ? `?source_id=${E.source}` : ""));
    E.items = r.items; E.sources = r.sources;
    if (E.want) { const i = E.items.findIndex(x => `${x.share}/${x.path}` === E.want); if (i >= 0) { E.sel = i; E.nameTouched = false; } E.want = null; }
    if (E.sel >= E.items.length) E.sel = Math.max(0, E.items.length - 1);
    if (!R.shelves.length) await loadShelves();
    if (!E.shelfId || !R.shelves.some(s => s.id === E.shelfId)) E.shelfId = (R.shelves.find(s => s.right === "edit") || R.shelves[0] || {}).id || null;
    if (E.shelfId) E.shelf = await api(`shelves/${E.shelfId}`);
  } catch (e) { m.innerHTML = `<div class="err">${esc(e.message)}</div>`; return; }
  inboxCount();
  if (!E.sources.length) {
    m.innerHTML = `<section class="card empty"><div style="font-size:42px">📥</div><h2>Kein Eingang eingerichtet</h2>
      <p>${isAdmin() ? "Trage unter <b>Verwaltung → Freigaben → Eingänge</b> den Ordner ein, in den dein Scanner speichert – z. B. <code>Scans/Eingang</code>."
        : "Ein Administrator muss erst einen Eingang einrichten – oder du hast in DSM keinen Zugriff auf den Scan-Ordner."}</p></section>`;
    return;
  }
  renderInbox();
}
window.inboxShow = inboxShow;

function renderInbox() {
  const m = $("eingangMain"), it = E.items[E.sel];
  m.innerHTML = `<div class="rtitle"><h2>📥 Eingang</h2><div class="row">
      <select data-src><option value="">Alle Eingänge (${E.sources.length})</option>${E.sources.map(s => `<option value="${s.id}">${esc(s.name)}</option>`).join("")}</select>
      <button data-reload>↻ Aktualisieren</button></div></div>
    ${E.items.length ? `<div class="inwrap">
      <section class="card inlist">${E.items.map((x, i) => `<div class="it ${i === E.sel ? "sel" : ""}" data-i="${i}" draggable="true" title="Auf einen Ordnerrücken ziehen">
          ${icoFor(x.mime, x.name)}<div><b>${esc(x.name)}</b><small>${esc(x.source)} · ${new Date(x.mtime * 1000).toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" })} · ${fmtBytes(x.size)}</small></div></div>`).join("")}
        <p class="muted small" style="margin:10px 4px 0">Ziehe eine Datei auf einen Ordnerrücken – oder wähle rechts das Ziel.</p></section>
      <section class="card inprev"><div class="pview" data-pv></div></section>
      <section class="card insort" data-form></section></div>`
    : '<section class="card empty"><div style="font-size:42px">✅</div><h2>Alles einsortiert</h2><p class="muted">Neue Scans erscheinen hier automatisch.</p></section>'}`;
  const src = m.querySelector("[data-src]"); src.value = E.source;
  src.onchange = () => { E.source = src.value; E.sel = 0; inboxShow(); };
  m.querySelector("[data-reload]").onclick = () => inboxShow();
  if (!it) return;
  $$("[data-i]", m).forEach(el => {
    el.onclick = () => { E.sel = Number(el.dataset.i); E.nameTouched = false; renderInbox(); };
    el.addEventListener("dragstart", (e) => { e.dataTransfer.setData("text/plain", "inbox:" + el.dataset.i); e.dataTransfer.effectAllowed = "move"; });
  });
  renderPreview(m.querySelector("[data-pv]"), `api/v1/browse/file?share=${encodeURIComponent(it.share)}&path=${encodeURIComponent(it.path)}`, it.mime);
  renderSortForm(it);
}

async function renderSortForm(it) {
  const box = $("eingangMain").querySelector("[data-form]");
  const edit = R.shelves.filter(s => s.right === "edit");
  if (!edit.length) { box.innerHTML = '<h2>Einsortieren</h2><p class="muted">Du darfst in keinem Regal Dokumente ablegen.</p>'; return; }
  const binders = E.shelf ? E.shelf.boards.flatMap(b => b.binders) : [];
  if (!binders.some(x => x.id === E.binderId)) E.binderId = binders.length ? binders[0].id : null;
  let sug = { title: "", doc_date: "", ext: "" };
  try { sug = await api(`inbox/suggest?share=${encodeURIComponent(it.share)}&path=${encodeURIComponent(it.path)}`); } catch (e) { /* egal */ }
  box.innerHTML = `<h2 style="margin:0">Einsortieren</h2>
    <div class="mini-shelf">${(E.shelf ? E.shelf.boards : []).map(b => `<div class="mrow">${b.binders.map(x =>
      `<div class="ms ${x.id === E.binderId ? "on" : ""}" data-mb="${x.id}" style="background:${esc(x.color)}" title="${esc(x.title)}"><span>${esc(x.title)}</span></div>`).join("") ||
      '<span class="muted small" style="color:#e9d5bf">leer</span>'}</div>`).join("")}</div>
    <div class="formgrid">
      <label>Regal <select data-k="shelf">${edit.map(s => `<option value="${s.id}">${esc(s.name)}</option>`).join("")}</select></label>
      <label>Ordner <select data-k="binder">${binders.map(x => `<option value="${x.id}">${esc(x.title)}</option>`).join("")}</select></label>
      <label class="span2">Register <select data-k="tab"></select></label>
      <label class="span2">Titel <input data-k="title" maxlength="200" value="${esc(sug.title)}" placeholder="z. B. Versicherungsschein 2026"></label>
      <label>Datum <input data-k="date" type="date" value="${esc(sug.doc_date)}"></label>
      <label>Schlagworte ${tagInputHtml([])}</label>
      <label class="span2">Neuer Dateiname <input data-k="fname" maxlength="220"></label></div>
    <div class="target" data-target></div>
    <div class="row"><button class="primary" data-go ${it.can_move ? "" : "disabled title=\"Du darfst in DSM im Scan-Ordner nicht schreiben\""}>Einsortieren</button>
      <button data-link title="Datei bleibt im Scan-Ordner">Nur verknüpfen</button></div><div class="err" data-err></div>`;
  const q = (k) => box.querySelector(`[data-k=${k}]`);
  q("shelf").value = String(E.shelfId);
  if (E.binderId) q("binder").value = String(E.binderId);
  const fname = () => {
    if (E.nameTouched) return;
    const t = q("title").value.trim();
    q("fname").value = t ? ((q("date").value ? q("date").value + " " : "") + t).replace(/[\\/:*?"<>|]/g, "_") + (sug.ext || "") : it.name;
  };
  fname();
  if (!sug.title) setTimeout(() => q("title").focus(), 0);
  q("title").oninput = fname; q("date").oninput = fname;
  q("fname").oninput = () => { E.nameTouched = true; };
  const loadTabs = async () => {
    if (!E.binderId) { q("tab").innerHTML = ""; showTarget(); return; }
    E.binder = await api(`binders/${E.binderId}`);
    q("tab").innerHTML = E.binder.tabs.map(t => `<option value="${t.id}">${esc(t.name)}</option>`).join("");
    showTarget();
  };
  const showTarget = () => {
    const el = box.querySelector("[data-target]");
    if (!E.binder) { el.innerHTML = '<span class="muted">Erst einen Ordner wählen.</span>'; return; }
    const tab = E.binder.tabs.find(t => String(t.id) === q("tab").value);
    const tg = (tab && tab.target) || E.binder.target;
    el.innerHTML = !it.can_move ? '<span class="muted">Du darfst im Scan-Ordner nicht schreiben – nur verknüpfen ist möglich.</span>'
      : tg ? `Wird verschoben nach <b>${esc(targetText(tg))}/</b><br><span class="muted small">Zielordner ${tab && tab.target ? `des Registers „${esc(tab.name)}“` : `des Ordners „${esc(E.binder.title)}“`}</span>`
      : `<span class="muted">„${esc(E.binder.title)}“ hat noch keinen Zielordner – beim Einsortieren fragt das Regal nach.</span>`;
  };
  q("shelf").onchange = async () => { E.shelfId = Number(q("shelf").value); E.shelf = await api(`shelves/${E.shelfId}`); E.binderId = null; renderSortForm(it); };
  q("binder").onchange = () => { E.binderId = Number(q("binder").value); markSpine(); loadTabs(); };
  q("tab").onchange = showTarget;
  const markSpine = () => $$("[data-mb]", box).forEach(s => s.classList.toggle("on", Number(s.dataset.mb) === E.binderId));
  $$("[data-mb]", box).forEach(s => {
    s.onclick = () => { E.binderId = Number(s.dataset.mb); q("binder").value = s.dataset.mb; markSpine(); loadTabs(); };
    s.addEventListener("dragover", (e) => { e.preventDefault(); s.classList.add("drop"); });
    s.addEventListener("dragleave", () => s.classList.remove("drop"));
    s.addEventListener("drop", async (e) => {
      e.preventDefault(); s.classList.remove("drop");
      const v = e.dataTransfer.getData("text/plain"); if (!v.startsWith("inbox:")) return;
      const i = Number(v.slice(6));
      if (i !== E.sel) { E.sel = i; E.nameTouched = false; E.binderId = Number(s.dataset.mb); renderInbox(); return; }  // erst ansehen, dann einsortieren
      E.binderId = Number(s.dataset.mb); q("binder").value = s.dataset.mb; markSpine(); await loadTabs();
      submit(it.can_move ? "move" : "link");
    });
  });
  const submit = async (mode, target) => {
    const err = box.querySelector("[data-err]"); err.textContent = "";
    if (!q("tab").value) { err.textContent = "Bitte Ordner und Register wählen."; return; }
    const body = { share: it.share, path: it.path, tab_id: Number(q("tab").value), title: q("title").value.trim() || null,
                   doc_date: q("date").value || null, tags: splitTags(q("tags").value), filename: q("fname").value.trim() || null, mode };
    if (target) body.target = target;
    $$("button", box).forEach(b => { b.disabled = true; });
    try {
      const r = await api("inbox/file", { method: "POST", body });
      E.nameTouched = false;
      toast(`„${body.title || it.name}“ ${r.moved ? "verschoben nach /" + r.share + "/" + r.path.split("/").slice(0, -1).join("/") : "verknüpft"}`, r.binder_id, r.doc_id);
      loadTags(); loadShelves();
      await inboxShow();
    } catch (e) {
      if (e.message === "no_target") {
        const t = await pickFolder({ title: `Zielordner für „${E.binder.title}“`, extra:
          `<p style="margin:0">Der Ordner <b>„${esc(E.binder.title)}“</b> hat noch keinen Zielordner. Wohin soll <b>${esc(it.name)}</b>?</p>
           <label class="inline"><input type="checkbox" data-remember checked> als Zielordner für „${esc(E.binder.title)}“ merken</label>`,
          altLabel: "Nur verknüpfen (bleibt im Scan-Ordner)" });
        if (t === "alt") return submit("link");
        if (t) return submit("move", t);
      } else err.textContent = e.message;
    } finally { $$("button", box).forEach(b => { b.disabled = false; }); if (!it.can_move) box.querySelector("[data-go]").disabled = true; }
  };
  box.querySelector("[data-go]").onclick = () => submit("move");
  box.querySelector("[data-link]").onclick = () => submit("link");
  await loadTabs();
}

function toast(text, binderId, docId) {
  const t = document.createElement("div");
  t.className = "toast";
  t.innerHTML = `✅ ${esc(text)} <button class="mini" data-open>Im Ordner ansehen</button>`;
  document.body.appendChild(t);
  t.querySelector("[data-open]").onclick = () => { t.remove(); showTab("regal", "b" + binderId).then(() => openBinder(binderId, docId)); };
  setTimeout(() => t.remove(), 6000);
}

// ---------- Verwaltung: Eingänge ----------
async function loadInboxSources(list) {
  const r = list || await api("admin/inbox-sources");
  $("inTable").innerHTML = r.length ? `<thead><tr><th>Aktiv</th><th>Name</th><th>Ordner</th><th>Zugriff des Regals</th><th></th></tr></thead><tbody>` +
    r.map(s => `<tr><td><input type="checkbox" data-inact="${s.id}" ${s.active ? "checked" : ""}></td><td><b>${esc(s.name)}</b></td>
      <td class="mono small">/${esc(s.share)}${s.path ? "/" + esc(s.path) : ""}</td>
      <td>${!s.ok ? '<span class="tag off">nicht gefunden</span>' : s.writable ? '<span class="ok">lesen &amp; schreiben</span>' : '<span class="tag warn">nur lesen</span> <span class="muted small">– verschieben geht nicht</span>'}</td>
      <td class="num"><button class="mini danger" data-indel="${s.id}">Entfernen</button></td></tr>`).join("") + "</tbody>"
    : '<tbody><tr><td class="muted">Noch kein Eingang. Trag den Ordner ein, in den dein Scanner speichert.</td></tr></tbody>';
  $$("#inTable [data-inact]").forEach(cb => cb.onchange = async () => {
    try { loadInboxSources(await api(`admin/inbox-sources/${cb.dataset.inact}`, { method: "PATCH", body: { active: cb.checked } })); } catch (e) { $("inErr").textContent = e.message; }
  });
  $$("#inTable [data-indel]").forEach(b => b.onclick = async () => {
    if (!confirm("Eingang entfernen? Die Dateien im Ordner bleiben unverändert.")) return;
    try { loadInboxSources(await api(`admin/inbox-sources/${b.dataset.indel}`, { method: "DELETE" })); } catch (e) { $("inErr").textContent = e.message; }
  });
}
window.loadInboxSources = loadInboxSources;
$("inNew").onclick = async () => {
  $("inErr").textContent = "";
  const t = await pickFolder({ title: "Eingangsordner wählen (z. B. Scans/Eingang)", writable: false });
  if (!t) return;
  const name = prompt("Name des Eingangs:", t.path ? t.path.split("/").pop() : t.share);
  if (name === null) return;
  try { loadInboxSources(await api("admin/inbox-sources", { method: "POST", body: { share: t.share, path: t.path, name } })); inboxCount(); }
  catch (e) { $("inErr").textContent = e.message; }
};
