"use strict";
const $ = (id) => document.getElementById(id);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const state = { me: null, settings: null, users: [], tab: null, branding: null };

// ---------- Hilfen ----------
// Sitzungs-Token zusätzlich zum Cookie: im DSM-Fenster (iframe) blocken Browser Cookies oft
const session = {
  get() { try { return sessionStorage.getItem("org_session") || ""; } catch (e) { return session.mem || ""; } },
  set(v) { session.mem = v; try { v ? sessionStorage.setItem("org_session", v) : sessionStorage.removeItem("org_session"); } catch (e) { /* privat */ } },
};
// Im DSM-Fenster läuft alles über app.cgi?p=… (gleicher Ursprung wie DSM, Single Sign-on)
const PROXY = window.ORG_PROXY || null;
const SYNO_TOKEN = (() => { try { return new URLSearchParams(location.search).get("SynoToken") || ""; } catch (e) { return ""; } })();
function authHeaders(extra = {}) {
  const t = session.get();
  const h = t ? { ...extra, "X-Session-Token": t } : { ...extra };
  if (SYNO_TOKEN) h["X-SYNO-TOKEN"] = SYNO_TOKEN;
  return h;
}
function url(path) {
  if (!PROXY) return path;
  return `${PROXY}?${SYNO_TOKEN ? "SynoToken=" + encodeURIComponent(SYNO_TOKEN) + "&" : ""}p=${encodeURIComponent("/" + path)}`;
}
async function api(path, opts = {}) {
  const method = (opts.method || "GET").toUpperCase();
  const extra = opts.body ? { "Content-Type": "application/json" } : {};
  let sendMethod = method;
  if (PROXY && ["PATCH", "PUT", "DELETE"].includes(method)) {
    // DSM reicht diese Methoden nicht an app.cgi durch → als POST mit Override schicken
    sendMethod = "POST";
    extra["X-HTTP-Method-Override"] = method;
  }
  let full = "api/v1/" + path;
  if (method === "GET") full += (full.includes("?") ? "&" : "?") + "_=" + Date.now();  // nie aus dem Cache
  const res = await fetch(url(full), {
    credentials: "same-origin", cache: "no-store", method: sendMethod,
    headers: authHeaders(extra), body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (res.status === 401 && path !== "auth/login") { session.set(""); showLogin(); throw new Error("Nicht angemeldet"); }
  const data = (res.headers.get("content-type") || "").includes("json") ? await res.json() : null;
  if (!res.ok) {
    const d = data && data.detail;
    const err = new Error(typeof d === "string" ? d : Array.isArray(d) ? d.map(x => x.msg).join(", ") : "Fehler " + res.status);
    err.status = res.status;
    throw err;
  }
  return data;
}
async function downloadFile(path, fallbackName) {
  // per fetch statt Link, damit es auch im DSM-Fenster mit Header-Login klappt
  const res = await fetch(url(path), { credentials: "same-origin", cache: "no-store", headers: authHeaders() });
  if (!res.ok) {
    let msg = "Fehler " + res.status;
    try { const d = await res.json(); if (typeof d.detail === "string") msg = d.detail; } catch (e) { /* egal */ }
    throw new Error("Download fehlgeschlagen: " + msg);
  }
  const name = ((res.headers.get("content-disposition") || "").match(/filename="([^"]+)"/) || [])[1] || fallbackName;
  const blobUrl = URL.createObjectURL(await res.blob());
  const a = document.createElement("a"); a.href = blobUrl; a.download = name; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(blobUrl), 4000);
}
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const p2 = (n) => String(n).padStart(2, "0");
const deDate = (iso) => iso ? `${iso.slice(8, 10)}.${iso.slice(5, 7)}.${iso.slice(0, 4)}` : "";
const fmtDT = (iso) => iso ? new Date(iso).toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" }) : "";
const okMsg = (el, text) => { el.innerHTML = `<span class="ok">${esc(text)}</span>`; setTimeout(() => { if (el.textContent === text) el.textContent = ""; }, 4000); };
const isAdmin = () => state.me && state.me.is_admin;
const initials = (name) => (name || "?").split(/\s+/).filter(Boolean).slice(0, 2).map(w => w[0]).join("").toUpperCase();
const store = { get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
                set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* egal */ } } };

// ---------- Start, Anmeldung, Reiter ----------
const TABS = ["regal", "eingang", "admin", "me"];
const DOC_TABS = ["regal", "eingang"];
const firstTab = () => "regal";
function showLogin() {
  $("appView").classList.add("hidden"); $("loginView").classList.remove("hidden");
  setTimeout(() => $("lu").focus(), 0);
}

async function login() {
  $("loginErr").textContent = "";
  try {
    const body = { username: $("lu").value, password: $("lp").value };
    if (!$("otpWrap").classList.contains("hidden")) body.otp_code = $("lo").value.trim();
    let r;
    try { r = await api("auth/login", { method: "POST", body }); }
    catch (e) {
      if (e.message === "otp_required") {
        $("otpWrap").classList.remove("hidden"); $("lo").focus();
        throw new Error("Bitte den Zwei-Faktor-Code deines DSM-Kontos eingeben.");
      }
      throw e;
    }
    session.set(r.session_token || "");
    $("lp").value = ""; $("lo").value = ""; $("otpWrap").classList.add("hidden");
    await start();
  } catch (e) { $("loginErr").textContent = e.message; }
}

function applyMode() {
  $$("#tabs button").forEach(b => b.classList.toggle("hidden", b.dataset.tab === "admin" && !isAdmin()));
  $$(".admin").forEach(el => el.classList.toggle("hidden", !isAdmin()));
}

async function start() {
  state.me = await api("me");
  state.settings = await api("settings");
  if (window.orgTheme) orgTheme(state.me.theme_effective || "classic");
  $("loginView").classList.add("hidden"); $("appView").classList.remove("hidden");
  showMe();
  $("httpWarn").classList.toggle("hidden", !state.me.insecure_http);
  showPortNotice();
  applyMode();
  if (window.inboxCount) inboxCount();
  const [wanted, sec] = ((location.hash || "").slice(1) || store.get("tab") || firstTab()).split("/");
  if (sec && wanted === "admin") store.set("asec", sec);
  await showTab(TABS.includes(wanted) && (wanted !== "admin" || isAdmin()) ? wanted : firstTab(), wanted === "admin" ? "" : sec);
}

function showPortNotice() {
  const pc = isAdmin() && state.settings && state.settings.port_changed;
  $("portWarn").classList.toggle("hidden", !pc);
  if (!pc) return;
  $("portWarn").innerHTML = `<b>Port geändert:</b> Port ${pc.old} war auf der NAS schon belegt (z. B. von Foto-Globus) – das Ordner-Regal läuft jetzt auf
    <b>${pc.new}</b>. Das DSM-Fenster funktioniert weiter. Bitte im Paket-Zentrum die Paketquelle auf
    <code>http://127.0.0.1:${pc.new}/spk</code> ändern und ggf. das Ziel im Reverse Proxy anpassen.
    <button class="mini" id="portWarnOk">Erledigt</button>`;
  $("portWarnOk").onclick = async () => { try { await api("settings/port-notice/dismiss", { method: "POST" }); } catch (e) { /* egal */ }
    state.settings.port_changed = null; showPortNotice(); };
}

function showMe() {
  $("userAv").textContent = initials(state.me.display_name);
  $("userName").textContent = state.me.display_name;
  $("userChip").title = "Mein Zugang" + (state.me.via_dsm ? " (über DSM angemeldet)" : "");
}

async function showTab(name, sub) {
  state.tab = name; state.sub = sub || "";
  $$("#tabs button").forEach(b => b.classList.toggle("active", b.dataset.tab === name));
  $("userChip").classList.toggle("active", name === "me");
  $$(".page").forEach(p => p.classList.toggle("hidden", p.dataset.page !== name));
  store.set("tab", name);
  history.replaceState(null, "", "#" + name + (name === "admin" ? "/" + (store.get("asec") || "einstellungen") : sub ? "/" + sub : ""));
  try {
    if (DOC_TABS.includes(name) && window.docShow) await docShow(name, sub || "");
    if (name === "admin") { showAdminSec(store.get("asec") || "einstellungen"); await loadAdmin(); }
    if (name === "me") loadMe();
  } catch (e) { console.error(e); }
}

// ---------- Verwaltung ----------
const ADMIN_SECS = ["benutzer", "freigaben", "einstellungen", "mail", "system", "protokoll"];
function showAdminSec(key) {
  if (!ADMIN_SECS.includes(key)) key = "einstellungen";
  $$("#adminNav [data-asec]").forEach(b => b.classList.toggle("active", b.dataset.asec === key));
  $$('[data-page="admin"] .asec').forEach(sec => sec.classList.toggle("hidden", sec.dataset.asec !== key));
  store.set("asec", key);
  if (state.tab === "admin") history.replaceState(null, "", "#admin/" + key);
}

async function loadAdmin() {
  state.settings = await api("settings");
  applyMode();
  fillSettings();
  const jobs = [loadUpdates().catch(e => { $("updErr").textContent = e.message; }),
                loadBackups().catch(e => { $("bkErr").textContent = e.message; })];
  state.users = await api("users");
  showUsers();
  jobs.push(initAudit().catch(e => { $("alErr").textContent = e.message; }));
  if (window.loadSharesAdmin) jobs.push(loadSharesAdmin().catch(e => { $("shErr").textContent = e.message; }));
  if (window.loadInboxSources) jobs.push(loadInboxSources().catch(e => { $("inErr").textContent = e.message; }));
  jobs.push(loadProxy().catch(e => { $("pxErr").textContent = e.message; }));
  await Promise.all(jobs);
}

// Benutzer
function showUsers() {
  const all = state.users.filter(u => u.active || $("uShowInactive").checked);
  $("usersTable").innerHTML = `<thead><tr><th>Name</th><th>Benutzername</th><th>E-Mail</th><th>Anmeldung</th><th></th></tr></thead><tbody>` +
    all.map(u => `<tr class="urow ${u.active ? "" : "inactive"}" data-id="${u.id}">
      <td><b>${esc(u.display_name)}</b>${u.is_admin ?  ' <span class="tag boss">Vollzugriff</span>' : ' <span class="tag">pro Regal</span>'}${u.active ? "" : ' <span class="tag off">deaktiviert</span>'}</td>
      <td>${esc(u.username)}</td><td>${esc(u.email || "")}</td>
      <td>${u.has_password ? "eigenes Passwort" : '<span title="meldet sich mit dem DSM-Konto an">nur DSM-Konto</span>'}</td>
      <td class="num"><button class="mini" data-uedit="${u.id}">Bearbeiten</button></td></tr>`).join("") + "</tbody>";
  $$("#usersTable [data-uedit]").forEach(b => b.onclick = () => openUserEdit(Number(b.dataset.uedit)));
}
function openUserEdit(id) {
  $$("#usersTable tr.uedit").forEach(r => r.remove());
  const u = state.users.find(x => x.id === id);
  const tr = $("usersTable").querySelector(`tr.urow[data-id="${id}"]`);
  if (!u || !tr) return;
  const row = document.createElement("tr");
  row.className = "uedit";
  row.innerHTML = `<td colspan="5"><div class="uform"><div class="ugrid">
      <label>Anzeigename <input data-f="display_name" value="${esc(u.display_name)}"></label>
      <label>E-Mail <input data-f="email" type="email" value="${esc(u.email || "")}"></label>
      <label>Neues Passwort <input data-f="password" type="password" autocomplete="new-password" placeholder="leer = unverändert"></label>
    </div><div class="row" style="margin-top:10px">
      <label class="inline"><input type="checkbox" data-f="is_admin" ${u.is_admin ? "checked" : ""}> Vollzugriff (Admin)</label>
      <label class="inline"><input type="checkbox" data-f="active" ${u.active ? "checked" : ""}> aktiv</label>
      <button class="primary" data-save>Speichern</button><button data-cancel>Abbrechen</button>
    </div><div class="ushares" data-ushares><span class="muted small">Freigaben werden geladen …</span></div></div></td>`;
  tr.after(row);
  if (window.userSharesBox) userSharesBox(id, row.querySelector("[data-ushares]"));
  row.querySelector("[data-cancel]").onclick = () => row.remove();
  row.querySelector("[data-save]").onclick = async () => {
    $("uErr").textContent = "";
    const f = (k) => row.querySelector(`[data-f="${k}"]`);
    const body = { display_name: f("display_name").value.trim(), email: f("email").value.trim() || null,
                   is_admin: f("is_admin").checked, active: f("active").checked };
    if (f("password").value) body.password = f("password").value;
    try {
      await api(`users/${id}`, { method: "PATCH", body });
      state.users = await api("users"); showUsers(); okMsg($("uErr"), "Gespeichert.");
      if (id === state.me.id) { state.me = await api("me"); showMe(); }
    } catch (e) { $("uErr").textContent = e.message; }
  };
}
async function createUser() {
  $("uErr").textContent = "";
  const body = { username: $("nuName").value.trim(), display_name: $("nuDisplay").value.trim() || $("nuName").value.trim(),
                 email: $("nuEmail").value.trim() || null, is_admin: $("nuAdmin").checked };
  if ($("nuPw").value) body.password = $("nuPw").value;
  try {
    await api("users", { method: "POST", body });
    ["nuName", "nuDisplay", "nuEmail", "nuPw"].forEach(id => { $(id).value = ""; });
    $("nuAdmin").checked = false; $("newUserBox").open = false;
    state.users = await api("users"); showUsers(); okMsg($("uErr"), "Benutzer angelegt.");
  } catch (e) { $("uErr").textContent = e.message; }
}

// Einstellungen
function fillSettings() {
  const s = state.settings;
  $("setTheme").innerHTML = Object.entries(s.themes).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
  $("setTheme").value = s.theme_default;
  $("setUrl").value = s.public_url || "";
  $("setUrl").placeholder = s.public_url_env || "https://doku.meinedomain.de";
  $("setDsmLogin").checked = s.dsm_login; $("setDsmCreate").checked = s.dsm_auto_create; $("setDsmAdmins").checked = s.dsm_admins;
  const du = $("setDsmUrl");
  if (s.dsm_url && ![...du.options].some(o => o.value === s.dsm_url)) du.insertAdjacentHTML("beforeend", `<option value="${esc(s.dsm_url)}">${esc(s.dsm_url)}</option>`);
  du.value = s.dsm_url || "http://127.0.0.1:5000";
  $("setDsmSso").checked = s.dsm_sso; $("setHttp").checked = s.http_access;
  const warn = $("setSsoWarn");
  warn.classList.toggle("hidden", !(s.dsm_sso && s.dsm_secret_mode === "world"));
  warn.innerHTML = '<span class="tag warn">Hinweis</span> Der Schlüssel für das DSM-Fenster liegt für alle lokalen Benutzer der NAS lesbar im Paketordner ' +
    '(er ließ sich auf dieser NAS nicht auf den Paketbenutzer beschränken). Er wird bei jedem Start neu erzeugt. Wer auf der NAS fremden ' +
    'Programmen oder Benutzern mit Konsolenzugang nicht traut, schaltet die Anmeldung im DSM-Fenster aus – dann meldet man sich auch dort mit Passwort an.';
  $("smtpHost").value = s.smtp_host || ""; $("smtpPort").value = s.smtp_port || 587; $("smtpSec").value = s.smtp_security || "starttls";
  $("smtpUser").value = s.smtp_user || ""; $("smtpPw").value = "";
  $("smtpPw").placeholder = s.smtp_password_set ? "gespeichert – leer = unverändert" : "";
  $("smtpFrom").value = s.smtp_from || ""; $("smtpFromName").value = s.smtp_from_name || "";
  if (!$("mailTestTo").value) $("mailTestTo").value = state.me.email || "";
  $("portVal").value = s.port;
  applyBranding(state.branding);
}

async function saveSettings() {
  $("setErr").textContent = "";
  if (state.settings.http_access && !$("setHttp").checked && location.protocol === "http:" && !window.ORG_PROXY &&
      !/^(localhost|127\.0\.0\.1|\[::1\])$/.test(location.hostname) &&
      !confirm("Du bist gerade selbst über http verbunden. Nach dem Speichern geht es in diesem Fenster nur noch über das DSM-Fenster oder https weiter. Fortfahren?")) return;
  try {
    const company = $("setCompany").value.trim();
    if (company !== ((state.branding && state.branding.company_name) || "")) {
      applyBranding(await api("branding", { method: "PATCH", body: { company_name: company } }));
    }
    state.settings = await api("settings", { method: "PATCH", body: {
      theme_default: $("setTheme").value, public_url: $("setUrl").value.trim(),
      dsm_login: $("setDsmLogin").checked, dsm_auto_create: $("setDsmCreate").checked, dsm_admins: $("setDsmAdmins").checked,
      dsm_url: $("setDsmUrl").value || null, dsm_sso: $("setDsmSso").checked, http_access: $("setHttp").checked } });
    state.me = await api("me");
    if (window.orgTheme) orgTheme(state.me.theme_effective);
    applyMode(); fillSettings();
    state.users = await api("users"); showUsers();
    okMsg($("setErr"), "Gespeichert.");
  } catch (e) { $("setErr").textContent = e.message; }
}

async function saveMail() {
  $("mailErr").textContent = "";
  const body = { smtp_host: $("smtpHost").value.trim(), smtp_port: Number($("smtpPort").value || 587),
                 smtp_security: $("smtpSec").value, smtp_user: $("smtpUser").value.trim(),
                 smtp_from: $("smtpFrom").value.trim(), smtp_from_name: $("smtpFromName").value.trim() };
  if ($("smtpPw").value) body.smtp_password = $("smtpPw").value;
  try { state.settings = await api("settings", { method: "PATCH", body }); fillSettings(); okMsg($("mailErr"), "Gespeichert."); }
  catch (e) { $("mailErr").textContent = e.message; }
}
async function testMail() {
  $("mailErr").textContent = ""; $("mailTest").disabled = true;
  try { const r = await api("settings/mail-test", { method: "POST", body: { to: $("mailTestTo").value.trim() } }); okMsg($("mailErr"), r.message); }
  catch (e) { $("mailErr").textContent = e.message; }
  finally { $("mailTest").disabled = false; }
}

// Port
async function savePort() {
  $("portErr").textContent = "";
  const port = Number($("portVal").value);
  if (port === state.settings.port) { okMsg($("portErr"), "Unverändert."); return; }
  if (!confirm(`Port auf ${port} ändern?\n\nDas Ordner-Regal startet sich dafür neu (wenige Sekunden).`)) return;
  $("portSave").disabled = true;
  try {
    const r = await api("settings/port", { method: "POST", body: { port } });
    $("portErr").innerHTML = '<span class="ok">Port geändert – Neustart läuft …</span>';
    // Im DSM-Fenster und hinter dem Reverse Proxy bleibt die Adresse gleich; direkt aufgerufen: neuer Port
    const direct = !PROXY && location.port && Number(location.port) !== port;
    setTimeout(() => { if (direct) location.href = r.direct_url + location.hash; else location.reload(); }, 5000);
  } catch (e) { $("portErr").textContent = e.message; $("portSave").disabled = false; }
}

// Datensicherung
const fmtSize = (b) => b > 1048576 ? (b / 1048576).toFixed(1).replace(".", ",") + " MB" : Math.max(1, Math.round(b / 1024)) + " KB";
function showBackups(o) {
  if (!$("bkHour").options.length) $("bkHour").innerHTML = Array.from({ length: 24 }, (_, h) => `<option value="${h}">${p2(h)}:00</option>`).join("");
  $("bkEnabled").checked = o.enabled; $("bkHour").value = String(o.hour);
  $("bkDir").value = o.dir || ""; $("bkKeep").value = o.keep_days;
  const status = !o.last ? "noch keine Sicherung" :
    `letzte Sicherung: ${fmtDT(o.last)}` + (o.last_status && o.last_status !== "ok" ? ` – <span class="err">Ordner: ${esc(o.last_status)}</span>` : "");
  $("bkInfo").innerHTML = status + (o.dir ? "" : ' · <span class="err">kein freigegebener Ordner – nur Paketordner</span>');
  const reasons = { "manuell": "von Hand", "vor-wiederherstellung": "vor Wiederherstellung" };
  // die neueste Sicherung (erste Zeile) ist geschützt und lässt sich nicht löschen
  $("bkTable").innerHTML = o.backups.length ? `<thead><tr><th><input type="checkbox" id="bkAll" title="alle auswählen" ${o.backups.length < 2 ? "disabled" : ""}></th>` +
    `<th>Datum</th><th>Uhrzeit</th><th>Art</th><th class="num">Größe</th><th>liegt in</th><th></th></tr></thead><tbody>` +
    o.backups.map((b, i) => `<tr><td>${i === 0 ? '<span class="muted small" title="Die neueste Sicherung ist geschützt">🔒</span>'
                                               : `<input type="checkbox" data-bksel="${esc(b.name)}">`}</td>
      <td>${deDate(b.date)}</td><td>${b.time}</td><td>${esc(reasons[b.reason] || "automatisch")}${i === 0 ? ' <span class="muted small">(neueste)</span>' : ""}</td>
      <td class="num">${fmtSize(b.size)}</td><td>${b.where.map(w => w === "lokal" ? "Paketordner" : "Freigabe").join(", ")}</td>
      <td><button class="mini" data-bkdl="${esc(b.name)}">Herunterladen</button>
          <button class="mini danger" data-bkrs="${esc(b.name)}">Wiederherstellen</button>
          ${i === 0 ? "" : `<button class="mini danger" data-bkrm="${esc(b.name)}" title="Sicherung löschen">🗑</button>`}</td></tr>`).join("") + "</tbody>"
    : '<tbody><tr><td class="muted">Noch keine Sicherungen.</td></tr></tbody>';
  const sel = () => $$("#bkTable [data-bksel]").filter(c => c.checked).map(c => c.dataset.bksel);
  const upd = () => { const n = sel().length; $("bkDel").classList.toggle("hidden", !n); $("bkDel").textContent = `${n} ausgewählte löschen`; };
  $$("#bkTable [data-bksel]").forEach(c => c.onchange = upd);
  if ($("bkAll")) $("bkAll").onchange = () => { $$("#bkTable [data-bksel]").forEach(c => { c.checked = $("bkAll").checked; }); upd(); };
  $("bkDel").onclick = () => deleteBackups(sel());
  $$("#bkTable [data-bkrm]").forEach(b => b.onclick = () => deleteBackups([b.dataset.bkrm]));
  upd();
  $$("#bkTable [data-bkdl]").forEach(b => b.onclick = () =>
    downloadFile(`api/v1/backups/${encodeURIComponent(b.dataset.bkdl)}/download`, b.dataset.bkdl).catch(e => { $("bkErr").textContent = e.message; }));
  $$("#bkTable [data-bkrs]").forEach(b => b.onclick = () => restoreBackup(b.dataset.bkrs));
}
async function loadBackups() { showBackups(await api("backups")); }
async function deleteBackups(names) {
  if (!names.length) return;
  const what = names.length === 1 ? `die Sicherung „${names[0]}“` : `${names.length} Sicherungen`;
  if (!confirm(`Wirklich ${what} löschen?\n\nSie wird im Paketordner und im freigegebenen Ordner entfernt und lässt sich nicht zurückholen.`)) return;
  $("bkErr").textContent = "";
  try {
    const r = await api("backups/delete", { method: "POST", body: { names } });
    showBackups(r);
    if (r.errors.length) $("bkErr").textContent = `${r.deleted.length} gelöscht, ${r.errors.length} nicht: ` + r.errors.map(e => e.error).join("; ");
    else okMsg($("bkErr"), r.deleted.length === 1 ? "Sicherung gelöscht." : `${r.deleted.length} Sicherungen gelöscht.`);
  } catch (e) { $("bkErr").textContent = e.message; }
}
async function saveBackups() {
  $("bkErr").textContent = "";
  try {
    showBackups(await api("backups/settings", { method: "PATCH", body: { enabled: $("bkEnabled").checked,
      hour: Number($("bkHour").value), keep_days: Number($("bkKeep").value || 30), dir: $("bkDir").value.trim() } }));
    okMsg($("bkErr"), "Gespeichert.");
  } catch (e) { $("bkErr").textContent = e.message; }
}
async function backupNow() {
  $("bkErr").textContent = ""; $("bkNow").disabled = true;
  try {
    const r = await api("backups", { method: "POST" });
    showBackups(r);
    if (r.shared_error) $("bkErr").textContent = "Im Paketordner gesichert, aber nicht im freigegebenen Ordner: " + r.shared_error;
    else okMsg($("bkErr"), r.shared ? "Gesichert: " + r.shared : "Gesichert (nur Paketordner).");
  } catch (e) { $("bkErr").textContent = e.message; }
  finally { $("bkNow").disabled = false; }
}
async function restoreBackup(name) {
  if (!confirm(`Sicherung „${name}“ wiederherstellen?\n\nAlle Änderungen seit dieser Sicherung gehen verloren. ` +
               "Der aktuelle Stand wird vorher automatisch gesichert und kann so wieder zurückgeholt werden.")) return;
  $("bkErr").textContent = "";
  try {
    const r = await api(`backups/${encodeURIComponent(name)}/restore`, { method: "POST", body: { confirm: name } });
    alert(`Wiederhergestellt. Der vorherige Stand liegt in „${r.safety_backup}“.` +
          (r.smtp_password_missing ? "\n\nDas Mail-Passwort wird aus Sicherheitsgründen nicht mitgesichert – bitte unter " +
                                     "Verwaltung → Mail neu eintragen." : ""));
    location.reload();
  } catch (e) { $("bkErr").textContent = e.message; }
}

// Updates (Paketquelle fürs Paket-Zentrum)
function showUpdates(u) {
  $("updSrc").textContent = u.source_url;
  const when = u.checked_at ? new Date(u.checked_at * 1000).toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" }) : "noch nicht";
  let txt = `Installiert: <b>${esc(u.installed)}</b>`;
  if (u.latest) txt += ` · neueste auf GitHub: <b>${esc(u.latest)}</b>`;
  if (u.update_available) txt += u.ready ? ' · <span class="ok"><b>Update bereit</b> – im Paket-Zentrum „Aktualisieren“</span>' : " · wird heruntergeladen …";
  else if (u.latest) txt += ' · <span class="ok">aktuell</span>';
  txt += ` <span class="muted small">(geprüft: ${when})</span>`;
  $("updInfo").innerHTML = txt;
  $("updErr").textContent = u.error ? "GitHub nicht erreichbar: " + u.error : "";
}
async function loadUpdates() { showUpdates(await api("updates")); }
async function checkUpdates() {
  $("updCheck").disabled = true; $("updCheck").textContent = "Prüfe …";
  try { showUpdates(await api("updates/check", { method: "POST" })); }
  catch (e) { $("updErr").textContent = e.message; }
  finally { $("updCheck").disabled = false; $("updCheck").textContent = "Jetzt prüfen"; }
}

// Änderungsprotokoll
const AUDIT_PAGE = 100;
const audit = { rows: [], last: null };
async function initAudit() {
  const cur = $("alUser").value;
  $("alUser").innerHTML = '<option value="">alle</option>' + state.users.map(u => `<option value="${u.id}">${esc(u.display_name)}</option>`).join("");
  $("alUser").value = cur;
  await loadAudit(false);
}
function auditFilter() {
  const p = new URLSearchParams();
  for (const [k, id] of [["user_id", "alUser"], ["from", "alFrom"], ["to", "alTo"], ["category", "alCat"], ["q", "alQ"]]) {
    const v = $(id).value.trim(); if (v) p.set(k, v);
  }
  return p;
}
async function loadAudit(more) {
  $("alErr").textContent = "";
  const p = auditFilter(); p.set("limit", AUDIT_PAGE);
  if (more && audit.last) p.set("before_id", audit.last);
  const r = await api("audit?" + p.toString());
  if (!$("alCat").dataset.ready) {
    $("alCat").innerHTML = '<option value="">alle</option>' + Object.entries(r.categories).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
    $("alCat").dataset.ready = "1";
  }
  const rows = r.entries;
  audit.rows = more ? audit.rows.concat(rows) : rows;
  audit.last = rows.length ? rows[rows.length - 1].id : audit.last;
  $("alTable").innerHTML = audit.rows.length ? `<thead><tr><th>Zeitpunkt</th><th>Wer</th><th>Betrifft</th><th>Aktion</th><th>Details</th></tr></thead><tbody>` +
    audit.rows.map(a => `<tr><td class="nowrap">${fmtDT(a.ts_local)}</td>
      <td>${a.actor === "System" ? '<span class="muted">System</span>' : esc(a.actor)}</td>
      <td>${a.subject && a.subject !== a.actor ? esc(a.subject) : a.subject ? '<span class="muted">selbst</span>' : ""}</td>
      <td>${esc(a.label)}</td><td class="small wrap">${esc(a.text)}</td></tr>`).join("") + "</tbody>"
    : '<tbody><tr><td class="muted">Keine Einträge für diese Auswahl.</td></tr></tbody>';
  $("alMore").classList.toggle("hidden", rows.length < AUDIT_PAGE);
  $("alCount").textContent = audit.rows.length ? `${audit.rows.length} Einträge angezeigt` : "";
}

// ---------- Mein Zugang ----------
const THEME_SW = { classic: ["#f4f5f7", "#ffffff", "#2b6cb0"], dsm: ["#eef2f7", "#ffffff", "#057feb"],
                   dark: ["#0b0d10", "#15181d", "#f5a524"], warm: ["#f6efe4", "#fffdf9", "#b5400b"] };
const THEME_INFO = { classic: "Blau, hell – bei dunklem Gerät automatisch dunkel", dsm: "Blau wie DSM 7, passt ins DSM-Fenster",
                     dark: "Immer dunkel, Bernstein als Akzent", warm: "Sandtöne, größere Schrift und Knöpfe" };
function loadMe() {
  $("meName").value = state.me.display_name; $("meEmail").value = state.me.email || "";
  $("meInfo").textContent = `Benutzername: ${state.me.username}` + (state.me.is_admin ? " · Vollzugriff" : " · Rechte pro Regal") +
    (state.me.via_dsm ? " · angemeldet über DSM" : "");
  $("pwCard").classList.toggle("hidden", !state.me.has_password);
  $("logoutBtn").classList.toggle("hidden", !!state.me.via_dsm);
  showThemes();
}
function showThemes() {
  const themes = state.settings.themes || {};
  const def = state.settings.theme_default || "classic";
  const mine = state.me.theme || "";
  const tile = (key, name, info, sw) => `<button class="theme-opt ${key === mine ? "on" : ""}" data-theme-key="${key}">
      <span class="sw">${sw.map(c => `<i style="background:${c}"></i>`).join("")}</span>
      <b>${esc(name)}</b><span class="muted small">${esc(info)}</span></button>`;
  $("themes").innerHTML = tile("", "Standard der Firma", `zurzeit: ${themes[def] || def}`, THEME_SW[def] || THEME_SW.classic) +
    Object.entries(themes).map(([k, v]) => tile(k, v, THEME_INFO[k] || "", THEME_SW[k] || THEME_SW.classic)).join("");
  $$("#themes [data-theme-key]").forEach(b => b.onclick = async () => {
    const key = b.dataset.themeKey || null;
    if (window.orgTheme) orgTheme(key || def);            // sofort zeigen
    try {
      state.me = await api("me", { method: "PATCH", body: { theme: key } });
      if (window.orgTheme) orgTheme(state.me.theme_effective);
      showThemes();
    } catch (e) { $("meErr").textContent = e.message; }
  });
}
async function saveMe() {
  $("meErr").textContent = "";
  try {
    state.me = await api("me", { method: "PATCH", body: { display_name: $("meName").value.trim(), email: $("meEmail").value.trim() } });
    showMe(); okMsg($("meErr"), "Gespeichert.");
  } catch (e) { $("meErr").textContent = e.message; }
}
async function savePassword() {
  $("pwErr").textContent = "";
  try {
    const r = await api("me/password", { method: "POST", body: { old_password: $("pwOld").value, new_password: $("pwNew").value } });
    session.set(r.session_token || "");
    $("pwOld").value = ""; $("pwNew").value = "";
    okMsg($("pwErr"), "Passwort geändert – andere Geräte sind abgemeldet.");
  } catch (e) { $("pwErr").textContent = e.message; }
}
async function logout() {
  try { await api("auth/logout", { method: "POST" }); } catch (e) { /* egal */ }
  session.set(""); showLogin();
}

// ---------- Firmenname und Logo ----------
async function loadBranding() {
  try {
    const res = await fetch(url("api/v1/branding"), { cache: "no-store" });
    if (res.ok) applyBranding(await res.json());
  } catch (e) { /* egal */ }
}
function applyBranding(b) {
  state.branding = b || { company_name: "", logo: null };
  const company = state.branding.company_name || "";
  $$(".brandCompany").forEach(el => { el.textContent = company; el.classList.toggle("hidden", !company); });
  document.title = company ? `Ordner-Regal – ${company}` : "Ordner-Regal";
  const src = state.branding.logo ? url(`api/v1/branding/logo?v=${state.branding.logo.version}`) : "";
  $$(".brandLogo").forEach(img => { img.classList.toggle("hidden", !src); if (src) img.src = src; });
  $$(".brand").forEach(h => h.classList.toggle("has-logo", !!src));
  $("logoPrev").classList.toggle("hidden", !src); if (src) $("logoPrev").src = src;
  $("logoDel").classList.toggle("hidden", !src);
  if (document.activeElement !== $("setCompany")) $("setCompany").value = company;
}
$("logoFile").onchange = () => {
  const f = $("logoFile").files[0]; $("logoErr").textContent = "";
  if (!f) return;
  if (f.size > 1024 * 1024) { $("logoErr").textContent = "Das Logo ist zu groß (höchstens 1 MB)"; return; }
  const rd = new FileReader();
  rd.onload = async () => {
    try { applyBranding(await api("branding/logo", { method: "PUT", body: { data_url: rd.result } })); okMsg($("logoErr"), "Logo gespeichert."); }
    catch (e) { $("logoErr").textContent = e.message; }
    $("logoFile").value = "";
  };
  rd.readAsDataURL(f);
};
$("logoDel").onclick = async () => {
  if (!confirm("Firmenlogo entfernen?")) return;
  try { applyBranding(await api("branding/logo", { method: "DELETE" })); } catch (e) { $("logoErr").textContent = e.message; }
};

// ---------- Ereignisse ----------
$("loginBtn").onclick = login;
["lu", "lp", "lo"].forEach(id => $(id).addEventListener("keydown", e => { if (e.key === "Enter") login(); }));
$$("#tabs button, #userChip").forEach(b => b.onclick = () => showTab(b.dataset.tab));
window.addEventListener("hashchange", () => {
  if (!state.me) return;
  const [t, sub] = (location.hash || "").slice(1).split("/");
  if (DOC_TABS.includes(t) && (t !== state.tab || (sub || "") !== (state.sub || ""))) showTab(t, sub || "");
});
$$("#adminNav [data-asec]").forEach(b => b.onclick = () => showAdminSec(b.dataset.asec));
$("uShowInactive").onchange = showUsers;
$("nuSave").onclick = createUser;
$("setBtn").onclick = saveSettings;
$("mailSave").onclick = saveMail;
$("mailTest").onclick = testMail;
$("portSave").onclick = savePort;
$("bkSave").onclick = saveBackups;
$("bkNow").onclick = backupNow;
$("updCheck").onclick = checkUpdates;
$("updCopy").onclick = () => { try { navigator.clipboard.writeText($("updSrc").textContent); okMsg($("updErr"), "Kopiert."); } catch (e) { /* egal */ } };
$("alGo").onclick = () => loadAudit(false).catch(e => { $("alErr").textContent = e.message; });
$("alMore").onclick = () => loadAudit(true).catch(e => { $("alErr").textContent = e.message; });
$("alCsv").onclick = () => downloadFile("api/v1/audit/export.csv?" + auditFilter().toString(), "aenderungsprotokoll.csv")
  .catch(e => { $("alErr").textContent = e.message; });
$("meSave").onclick = saveMe;
$("pwSave").onclick = savePassword;
$("logoutBtn").onclick = logout;
fetch(url("api/v1/health")).then(r => r.json()).then(h => { $("version").textContent = h.version; }).catch(() => {});

loadBranding();
start().catch(showLogin);

// ---------- Tabellen sortieren (Klick auf den Spaltenkopf; zweiter Klick absteigend, dritter wie geliefert) ----------
const SORTS = (() => { try { return JSON.parse(localStorage.getItem("org_sort") || "{}"); } catch (e) { return {}; } })();
const sortColl = new Intl.Collator("de", { numeric: true, sensitivity: "base" });
function sortKey(row, col) {
  if (row.classList.contains("free") && col !== 0) return null;
  const td = row.cells[col];
  if (!td || td.colSpan > 1) return null;
  const v = (td.dataset.sort != null ? td.dataset.sort : td.textContent).trim();
  if (!v || v === "–" || v === "-") return null;
  const ip = v.match(/^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?!\d)/);
  if (ip) return ip.slice(1).reduce((a, x) => a * 256 + Number(x), 0);
  if (/^-?\d+(?:[.,]\d+)?$/.test(v)) return Number(v.replace(",", "."));
  return v;
}
function sortTable(table) {
  if (!table || !table.tHead || !table.tBodies[0]) return;
  const st = SORTS[table.id];
  [...table.tHead.rows[0].cells].forEach((th, i) => {
    if (th.querySelector("input")) return;
    th.classList.add("sorth");
    th.classList.toggle("asc", !!st && st.col === i && st.dir === 1);
    th.classList.toggle("desc", !!st && st.col === i && st.dir === -1);
    th.title = "Sortieren";
  });
  const tb = table.tBodies[0];
  const rows = [...tb.rows];
  rows.forEach((r, i) => { if (r.dataset.o == null) r.dataset.o = i; });
  if (rows.some(r => r.querySelector(".emptyrow"))) return;
  rows.sort((a, b) => {
    if (!st) return a.dataset.o - b.dataset.o;
    const ka = sortKey(a, st.col), kb = sortKey(b, st.col);
    if (ka == null || kb == null) return ka == null && kb == null ? a.dataset.o - b.dataset.o : ka == null ? 1 : -1;
    const c = typeof ka === "number" && typeof kb === "number" ? ka - kb : sortColl.compare(String(ka), String(kb));
    return (c || a.dataset.o - b.dataset.o) * (c ? st.dir : 1);
  });
  rows.forEach(r => tb.appendChild(r));
}
document.addEventListener("click", (e) => {
  const th = e.target.closest("table.sortable thead th");
  if (!th || th.querySelector("input")) return;
  const table = th.closest("table"), col = th.cellIndex, st = SORTS[table.id];
  if (!st || st.col !== col) SORTS[table.id] = { col, dir: 1 };
  else if (st.dir === 1) SORTS[table.id] = { col, dir: -1 };
  else delete SORTS[table.id];
  try { localStorage.setItem("org_sort", JSON.stringify(SORTS)); } catch (err) { /* egal */ }
  sortTable(table);
});

// ---------- Eigener Reverse Proxy (Verwaltung → System) ----------
async function loadProxy() {
  const r = await api("settings/proxy");
  $("pxVal").value = r.trusted_proxies || "";
  const via = r.peer && r.peer !== r.client;
  $("pxInfo").innerHTML = (r.env ? `<span class="muted">Zusätzlich aus ORG_TRUSTED_PROXIES: <span class="mono">${esc(r.env)}</span>.</span><br>` : "") +
    (via ? `Diese Anfrage kam über den Proxy <span class="mono">${esc(r.peer)}</span> von <span class="mono">${esc(r.client)}</span> ✓`
      : r.forwarded ? `<span class="tag warn">Achtung</span> Diese Anfrage kam von <span class="mono">${esc(r.peer || r.client)}</span> mit X-Forwarded-For
          <span class="mono">${esc(r.forwarded)}</span> – das ist vermutlich dein Proxy. Seine IP hier eintragen.`
      : `<span class="muted">Diese Anfrage kam direkt von <span class="mono">${esc(r.client)}</span>.</span>`);
}
$("pxSave").onclick = async () => {
  $("pxErr").textContent = "";
  try {
    await api("settings/proxy", { method: "PUT", body: { trusted_proxies: $("pxVal").value } });
    await loadProxy(); okMsg($("pxErr"), "Gespeichert.");
  } catch (e) { $("pxErr").textContent = e.message; }
};


