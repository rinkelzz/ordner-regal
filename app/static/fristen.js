"use strict";
// Ordner-Regal 0.4: Fristen & Wiedervorlage – Übersicht, Frist-Dialog, Kasten beim Dokument, Mail-Einstellungen.
const F = { kinds: null, repeats: null, status: "open", mine: false };

const dueText = (days) => days < 0 ? `seit ${-days} Tag${days === -1 ? "" : "en"} überfällig` : days === 0 ? "heute" :
  days === 1 ? "morgen" : `in ${days} Tagen`;
const dueCls = (days) => days < 0 ? "over" : days <= 7 ? "soon" : days <= 30 ? "near" : "";

async function kindsLoad() {
  if (!F.kinds) { const r = await api("deadline-kinds"); F.kinds = r.kinds; F.repeats = r.repeats; }
}

// ---------- Zähler im Reiter + Seitenleiste im Regal ----------
async function deadlineCount() {
  try {
    const r = await api("deadlines/summary");
    const n = r.overdue + r.week, b = $("dlBadge");
    b.textContent = String(n); b.classList.toggle("hidden", !n); b.classList.toggle("warnb", !r.overdue);
    const box = $("dueList");
    if (box) {
      box.innerHTML = r.next.length ? r.next.map(d => `<button class="dueItem" data-dgo="${d.places[0] ? d.places[0].binder_id : ""}" data-ddoc="${d.document_id}">
          <b>${esc(d.title)}</b><span>${esc(d.doc_title)}</span><span class="due ${dueCls(d.days)}">${deDate(d.due_on)} · ${dueText(d.days)}</span></button>`).join("")
        : '<span class="muted small">Keine offenen Fristen.</span>';
      $$("[data-dgo]", box).forEach(el => el.onclick = () => { if (el.dataset.dgo) openBinder(Number(el.dataset.dgo), Number(el.dataset.ddoc)); });
    }
  } catch (e) { /* egal */ }
}
window.deadlineCount = deadlineCount;
setInterval(() => { if (state.me) deadlineCount(); }, 120000);

// ---------- Reiter „Fristen“ ----------
async function fristenShow() {
  const m = $("fristenMain");
  let items;
  try { await kindsLoad(); items = await api(`deadlines?status=${F.status}${F.mine ? "&mine=1" : ""}`); }
  catch (e) { m.innerHTML = `<div class="err">${esc(e.message)}</div>`; return; }
  deadlineCount();
  const groups = F.status === "done" ? [["Erledigt", items]] : [
    ["Überfällig", items.filter(d => d.days < 0)], ["Diese Woche", items.filter(d => d.days >= 0 && d.days <= 7)],
    ["Nächste 30 Tage", items.filter(d => d.days > 7 && d.days <= 30)], ["Später", items.filter(d => d.days > 30)]];
  m.innerHTML = `<div class="rtitle"><h2>⏰ Fristen &amp; Wiedervorlage</h2><div class="row">
      <div class="seg"><button data-st="open" class="${F.status === "open" ? "on" : ""}">Offen</button><button data-st="done" class="${F.status === "done" ? "on" : ""}">Erledigt</button></div>
      <label class="inline small"><input type="checkbox" data-mine ${F.mine ? "checked" : ""}> nur meine</label></div></div>
    ${items.length ? groups.filter(g => g[1].length).map(([head, list]) => `<section class="card dlgroup"><h3>${esc(head)} <span class="muted">${list.length}</span></h3>
      ${list.map(rowHtml).join("")}</section>`).join("")
      : `<section class="card empty"><div style="font-size:42px">⏰</div><h2>${F.status === "done" ? "Noch nichts erledigt" : "Keine offenen Fristen"}</h2>
        <p class="muted">Fristen legst du direkt am Dokument an: Ordner öffnen → Dokument wählen → „＋ Frist“.</p></section>`}`;
  $$("[data-st]", m).forEach(b => b.onclick = () => { F.status = b.dataset.st; fristenShow(); });
  m.querySelector("[data-mine]").onchange = (e) => { F.mine = e.target.checked; fristenShow(); };
  bindRows(m, fristenShow);
}
window.fristenShow = fristenShow;

function rowHtml(d) {
  const p = d.places[0];
  const rec = d.recipients.users.map(u => u.name).concat(d.recipients.emails);
  return `<div class="dlrow ${d.done_at ? "done" : ""}">
    <div class="dldate ${d.done_at ? "" : dueCls(d.days)}"><b>${deDate(d.due_on)}</b><small>${d.done_at ? "erledigt " + fmtDT(d.done_at) : dueText(d.days)}</small></div>
    <div class="dlmain"><span><b>${esc(d.title)}</b>${d.repeat ? ` <span class="tag">🔁 ${esc(d.repeat_label)}</span>` : ""}</span>
      <a data-dgo="${p ? p.binder_id : ""}" data-ddoc="${d.document_id}">📄 ${esc(d.doc_title)}</a>
      <small class="muted">${p ? esc(p.shelf + " › " + p.binder + " › " + p.tab) : ""}${rec.length ? " · ✉️ " + esc(rec.join(", ")) : ""}</small>
      ${d.note ? `<small>${esc(d.note)}</small>` : ""}</div>
    <div class="row dlbtn">${d.can_edit ? (d.done_at ? `<button class="mini" data-reopen="${d.id}">Wieder öffnen</button>`
      : `<button class="mini primary" data-done="${d.id}">✓ Erledigt</button><button class="mini" data-dedit="${d.id}">Bearbeiten</button>`) : ""}</div></div>`;
}
function bindRows(root, reload, list) {
  $$("[data-dgo]", root).forEach(a => a.onclick = () => {
    if (!a.dataset.dgo) return;
    showTab("regal", "b" + a.dataset.dgo).then(() => openBinder(Number(a.dataset.dgo), Number(a.dataset.ddoc)));
  });
  $$("[data-done]", root).forEach(b => b.onclick = async () => {
    try {
      const r = await api(`deadlines/${b.dataset.done}/done`, { method: "POST" });
      if (r.next_due) toastMsg(`Erledigt – nächster Termin: ${deDate(r.next_due)}`);
      reload(); deadlineCount(); refreshShelfDots();
    } catch (e) { alert(e.message); }
  });
  $$("[data-reopen]", root).forEach(b => b.onclick = async () => { try { await api(`deadlines/${b.dataset.reopen}/reopen`, { method: "POST" }); reload(); deadlineCount(); } catch (e) { alert(e.message); } });
  $$("[data-dedit]", root).forEach(b => b.onclick = async () => {
    const all = list || await api(`deadlines?status=open`);
    const d = all.find(x => x.id === Number(b.dataset.dedit));
    if (d) deadlineForm(d.document_id, d, reload);
  });
}
function toastMsg(text) {
  const t = document.createElement("div"); t.className = "toast"; t.textContent = "✅ " + text;
  document.body.appendChild(t); setTimeout(() => t.remove(), 5000);
}
function refreshShelfDots() { if (R.shelf && !R.binder && state.tab === "regal") openShelf(R.shelf.id); }

// ---------- Frist anlegen / bearbeiten ----------
async function deadlineForm(docId, d, onSaved) {
  await kindsLoad();
  let cand = [];
  try { cand = await api(`docs/${docId}/recipient-candidates`); } catch (e) { /* egal */ }
  const chosen = new Set(d ? d.recipients.users.map(u => u.id) : cand.filter(c => c.me).map(c => c.id));
  const card = sheet(`<div class="toolbar"><h2>${d ? "Frist bearbeiten" : "Neue Frist"}</h2><button data-x>✕</button></div>
    <div class="formgrid">
      <label>Art <select data-k="kind">${Object.entries(F.kinds).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("")}</select></label>
      <label>Eigener Name <input data-k="label" maxlength="80" placeholder="optional, z. B. „Kündigen bis“"></label>
      <label>Datum <input data-k="due" type="date"></label>
      <label>Wiederholen <select data-k="repeat">${Object.entries(F.repeats).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("")}</select></label>
      <label>Erinnerung vorher <select data-k="lead">${[0, 1, 3, 7, 14, 30, 60, 90].map(n => `<option value="${n}">${n ? n + " Tage vorher" : "keine Vorab-Mail"}</option>`).join("")}</select></label>
      <label class="span2">Notiz (steht in der Mail) <textarea data-k="note" rows="2" maxlength="2000" placeholder="z. B. per Einschreiben kündigen"></textarea></label>
    </div>
    <div><span class="small"><b>Erinnerung an</b> (am Tag selbst, ${d ? "" : "danach "}wöchentlich nachfassen und in der Sammelmail):</span>
      <div class="rcpts">${cand.map(c => `<label class="inline small"><input type="checkbox" data-u="${c.id}" ${chosen.has(c.id) ? "checked" : ""}>
        ${esc(c.name)}${c.email ? "" : ' <span class="tag warn" title="Keine Mailadresse hinterlegt – unter „Mein Zugang“ bzw. Verwaltung → Benutzer eintragen">ohne Mail</span>'}</label>`).join("")}</div>
      <label>Weitere Mailadressen (durch Komma getrennt) <input data-k="emails" placeholder="buero@beispiel.de"></label></div>
    <div class="row"><button class="primary" data-save>Speichern</button><button data-x>Abbrechen</button>
      ${d ? '<button class="danger" data-del style="margin-left:auto">Frist löschen</button>' : ""}</div><div class="err" data-err></div>`, "narrow");
  const q = (k) => card.querySelector(`[data-k=${k}]`);
  q("kind").value = d ? d.kind : "kuendigung"; q("label").value = d ? d.label : ""; q("due").value = d ? d.due_on : "";
  q("repeat").value = d ? d.repeat : ""; q("lead").value = String(d ? d.lead_days : 14); q("note").value = d ? d.note : "";
  q("emails").value = d ? d.recipients.emails.join(", ") : "";
  if (![...q("lead").options].some(o => o.value === q("lead").value)) q("lead").insertAdjacentHTML("beforeend", `<option value="${d.lead_days}" selected>${d.lead_days} Tage vorher</option>`);
  const kindHint = () => { q("label").placeholder = q("kind").value === "eigene" ? "Name der Frist (Pflicht)" : "optional"; };
  q("kind").onchange = kindHint; kindHint();
  setTimeout(() => q("due").focus(), 0);
  card.querySelector("[data-save]").onclick = busy(card, async () => {
    const body = { kind: q("kind").value, label: q("label").value.trim(), due_on: q("due").value || null, repeat: q("repeat").value,
                   lead_days: Number(q("lead").value), note: q("note").value,
                   user_ids: $$("[data-u]", card).filter(c => c.checked).map(c => Number(c.dataset.u)),
                   emails: q("emails").value.split(/[,;\s]+/).map(x => x.trim()).filter(Boolean) };
    if (!body.due_on) throw new Error("Bitte ein Datum wählen");
    if (d) await api(`deadlines/${d.id}`, { method: "PATCH", body }); else await api(`docs/${docId}/deadlines`, { method: "POST", body });
    closeSheet(); deadlineCount(); if (onSaved) onSaved();
  });
  const del = card.querySelector("[data-del]");
  if (del) del.onclick = busy(card, async () => {
    if (!confirm("Frist löschen? Es kommen dann keine Erinnerungen mehr.")) return;
    await api(`deadlines/${d.id}`, { method: "DELETE" }); closeSheet(); deadlineCount(); if (onSaved) onSaved();
  });
}
window.deadlineForm = deadlineForm;

// Kasten „Fristen“ im geöffneten Dokument
async function docDeadlines(box, doc) {
  let list = [];
  try { list = await api(`docs/${doc.id}/deadlines`); } catch (e) { return; }
  const open = list.filter(d => !d.done_at);
  const el = document.createElement("div");
  el.className = "frist";
  el.innerHTML = `<div class="toolbar" style="margin:0 0 6px"><b>⏰ Fristen</b>${doc.can_edit ? '<button class="mini" data-new>＋ Frist</button>' : ""}</div>
    ${open.length ? open.map(d => `<div class="fr ${dueCls(d.days)}"><div><b>${esc(d.title)}</b> ${deDate(d.due_on)}${d.repeat ? " 🔁" : ""}
        <small>${dueText(d.days)}${d.lead_days ? ` · Mail ${d.lead_days} Tage vorher` : ""}</small></div>
        ${doc.can_edit ? `<span class="row"><button class="mini" data-done="${d.id}" title="Erledigt">✓</button><button class="mini" data-dedit="${d.id}">✎</button></span>` : ""}</div>`).join("")
      : '<span class="muted small">Keine offene Frist.</span>'}`;
  box.appendChild(el);
  const reload = () => { openBinder(R.binder.id, doc.id); deadlineCount(); };
  const n = el.querySelector("[data-new]"); if (n) n.onclick = () => deadlineForm(doc.id, null, reload);
  bindRows(el, reload, list);
}
window.docDeadlines = docDeadlines;

// ---------- Verwaltung → Mail: Fristen-Mails ----------
async function loadDeadlineSettings(st) {
  const r = st || await api("deadline-settings");
  if (!$("fmHour").options.length) {
    $("fmHour").innerHTML = Array.from({ length: 24 }, (_, h) => `<option value="${h}">${p2(h)}:00</option>`).join("");
    $("fmDay").innerHTML = r.weekdays.map((w, i) => `<option value="${i}">${w}</option>`).join("");
  }
  $("fmHour").value = String(r.hour); $("fmDigest").checked = r.digest; $("fmDay").value = String(r.digest_weekday);
  $("fmOver").value = String(r.overdue_days);
  $("fmInfo").innerHTML = (r.smtp_ok ? "" : '<span class="tag warn">Noch kein Mailserver</span> – unten unter <b>Mailserver (SMTP)</b> eintragen. ') +
    (r.last_result ? `Letzter Lauf: ${esc(r.last_result)}` : "") +
    (r.users_without_email.length ? `<br><span class="tag warn">Ohne Mailadresse</span> ${esc(r.users_without_email.join(", "))} – bekommt keine Erinnerungen.` : "");
}
window.loadDeadlineSettings = loadDeadlineSettings;
$("fmSave").onclick = async () => {
  $("fmErr").textContent = "";
  try {
    await loadDeadlineSettings(await api("deadline-settings", { method: "PATCH", body: { hour: Number($("fmHour").value), digest: $("fmDigest").checked,
      digest_weekday: Number($("fmDay").value), overdue_days: Number($("fmOver").value) } }));
    okMsg($("fmErr"), "Gespeichert.");
  } catch (e) { $("fmErr").textContent = e.message; }
};
$("fmRun").onclick = async () => {
  $("fmErr").textContent = ""; $("fmRun").disabled = true;
  try {
    const r = await api("deadline-settings/run-now", { method: "POST" });
    await loadDeadlineSettings(r);
    okMsg($("fmErr"), `${r.result.sent} Erinnerung(en) und ${r.result.digest} Sammelmail(s) verschickt.`);
  } catch (e) { $("fmErr").textContent = e.message; }
  finally { $("fmRun").disabled = false; }
};
