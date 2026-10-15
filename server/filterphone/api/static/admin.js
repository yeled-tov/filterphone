// ממשק הניהול של FilterPhone. בלי ספריות; כל טקסט מהשרת נכנס דרך textContent (בלי innerHTML).
"use strict";

let token = sessionStorage.getItem("fp_token") || "";
let catalog = null;           // רמות, קטגוריות ואפשרויות — נטען פעם אחת
let refreshTimer = null;
const ACTIONS = { block: "נחסם", replace: "נצבע", site: "אתר חסום", video: "וידאו נחסם" };
const KINDS = { image: "תמונה", inline: "תמונה מוטמעת", host: "אתר/שירות", video: "וידאו" };
const STATUS = { active: ["on", "פעיל"], disabled: ["off", "מושבת"], pending: ["wait", "ממתין לאישור"], rejected: ["off", "נדחה"] };
const PAYMENT = { unpaid: ["bad", "לא שולם"], paid: ["ok", "שולם"], free: ["accent", "חינם"] };
const MECH = { network: ["accent", "רשת"], device: ["violet", "בטלפון"], proxy: ["accent", "סינון"] };
// מצב תאימות (מחושב בשרת מהדיווח של הטלפון ומ-WireGuard)
const COMPLIANCE = {
  green: ["ok", "מוגן"], yellow: ["warn", "חלקי"], red: ["bad", "ההגנה לא פועלת"], inactive: ["", "לא פעיל"],
};
const DEVICE_FILTERS = {
  "": "כל המכשירים", green: "מוגנים (ירוק)", yellow: "חלקי (צהוב)", red: "ההגנה לא פועלת (אדום)",
  offline: "לא מחוברים", vpn_inactive: "VPN לא פעיל", accessibility_off: "מגן FilterPhone כבוי",
  unmanaged: "בלי מנהל מכשיר", sync: "מדיניות לא סונכרנה",
};
const APP_MODES = {
  "": ["", "פתוח"], network: ["accent", "רשת"], accessibility: ["violet", "בטלפון"], both: ["bad", "רשת + טלפון"],
};
const DPC_LABELS = {
  block_uninstall: ["מניעת הסרה של FilterPhone", "אי אפשר להסיר את האפליקציה מהטלפון."],
  always_on_vpn: ["VPN תמיד מופעל + חסימה בלי VPN", "הסינון קבוע; בלי VPN אין אינטרנט בכלל."],
  disallow_config_vpn: ["חסימת הגדרות VPN", "אי אפשר לנתק או להחליף VPN."],
  disallow_factory_reset: ["חסימת איפוס מההגדרות", "איפוס יצרן מתוך מסך ההגדרות חסום. איפוס בכפתורי החומרה (מצב שחזור) — אפשרי תמיד; ראו FRP."],
  disallow_safe_boot: ["חסימת מצב בטוח", "מצב בטוח עוקף אפליקציות — חסום."],
  disallow_add_user: ["חסימת משתמשים נוספים", "אי אפשר ליצור משתמש / אורח (שבהם אין סינון)."],
  disallow_unknown_sources: ["חסימת התקנת APK", "אי אפשר להתקין אפליקציות מקבצים."],
  disallow_private_dns: ["חסימת DNS פרטי", "אי אפשר להגדיר DNS שעוקף את הסינון."],
  disallow_debugging: ["חסימת ניפוי באגים (ADB)", "מומלץ אחרי שהמכשיר עובד. כשזה פעיל קשה יותר לתקן תקלות מרחוק."],
  disallow_install_apps: ["חסימת התקנת אפליקציות", "גם מהחנות — רק המנהל מוסיף אפליקציות."],
  disallow_modify_accounts: ["חסימת הוספת חשבונות", "אי אפשר להוסיף / להסיר חשבון Google ואחרים."],
  hide_blocked_apps: ["הסתרת אפליקציות חסומות", "אפליקציה חסומה נעלמת מהטלפון (במקום חסימה בנגישות)."],
  suspend_blocked_apps: ["אפליקציות חסומות באפור", "האייקון נשאר אפור; לחיצה מציגה 'חסום על ידי מנהל הסינון'. כשפעיל — במקום הסתרה. Google Play נחסם כך בלי לגעת בשירותי Google."],
  disallow_tethering: ["חסימת נקודה חמה", "שיתוף אינטרנט ב-Wi-Fi, בלוטות' ו-USB — אפור בהגדרות ובלוח המהיר."],
};
const ANDROID = { 26: "8.0", 27: "8.1", 28: "9", 29: "10", 30: "11", 31: "12", 32: "12L", 33: "13", 34: "14", 35: "15", 36: "16" };
const LOG_KINDS = {
  vpn_up: ["ok", "VPN חזר"], vpn_down: ["bad", "VPN כבה"], accessibility_on: ["ok", "המגן הופעל"],
  accessibility_off: ["bad", "המגן כובה"], management_on: ["ok", "מנהל מכשיר פעיל"],
  management_removed: ["bad", "מנהל המכשיר הוסר"], battery_unrestricted: ["ok", "סוללה: ללא הגבלה"],
  battery_restricted: ["warn", "סוללה: מוגבלת"], outside_vpn: ["bad", "דיווח מחוץ למנהרה"], policy_error: ["warn", "שגיאת מדיניות"],
  app_updated: ["ok", "האפליקציה עודכנה"], app_update_failed: ["warn", "עדכון האפליקציה נכשל"],
  paused: ["warn", "ההגנה הושהתה"], resumed: ["ok", "ההגנה חזרה"], unmanage_requested: ["warn", "נשלחה הסרת ניהול"],
  management_cleared: ["warn", "הניהול הוסר"], uninstalled: ["bad", "הלקוח הסיר את ההגנה (בהשהיה)"],
  uninstall_blocked: ["bad", "ניסיון הסרה בלי השהיה — נחסם"], transferred: ["warn", "הוחלף למכשיר אחר"],
  restored: ["ok", "ההגנה שוחזרה (אותו טלפון)"], transfer_in: ["ok", "הועבר מטלפון קודם"],
  transfer_started: ["warn", "נוצר קוד החלפה"], transfer_done: ["warn", "הוחלף למכשיר אחר"], rekey: ["", "מפתח VPN חודש"],
};
const UPDATE_STATES = {
  available: ["warn", "גרסה חדשה זמינה — הטלפון צריך להתקין ידנית"],
  waiting: ["", "ממתין ל-Wi-Fi לעדכון"],
  installing: ["", "מתקין עדכון..."],
  failed: ["bad", "העדכון נכשל"],
};

const APP_IMAGES = {
  auto: ["לפי הרמה", "ברמות 1–2 חסום, ברמה 3 מותר"],
  block: ["חסום", "תמונות באפליקציות שאי אפשר לסנן (אינסטגרם, אליאקספרס, גוגל...) לא נטענות"],
  allow: ["מותר", "האפליקציות מציגות תמונות בלי סינון (DNS עדיין חוסם אתרים)"],
};

// ---------------------------------------------------------------- עזרים

const $ = (sel, root = document) => root.querySelector(sel);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "dataset") Object.assign(node.dataset, v);
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else if (k === "checked" || k === "disabled" || k === "selected" || k === "value") node[k] = v;
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    method: opts.method || "GET",
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  });
  if (res.status === 401 && !path.endsWith("/login")) { showLogin(); throw new Error("נדרשת התחברות"); }
  if (res.status === 204) return null;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `שגיאה ${res.status}`);
  return data;
}

function toast(msg, err = false) {
  const t = el("div", { class: `toast${err ? " err" : ""}`, role: err ? "alert" : "status" }, msg);
  $("#toasts").append(t);
  setTimeout(() => t.remove(), err ? 6000 : 2600);
}

async function attempt(fn, okMsg) {
  try {
    const r = await fn();
    if (okMsg) toast(okMsg);
    return r;
  } catch (e) {
    toast(e.message, true);
    throw e;
  }
}

function ago(ts) {
  if (!ts) return "—";
  const s = Math.floor(Date.now() / 1000) - ts;
  if (s < 90) return "עכשיו";
  if (s < 3600) return `לפני ${Math.round(s / 60)} דק'`;
  if (s < 86400) return `לפני ${Math.round(s / 3600)} שע'`;
  const d = Math.round(s / 86400);
  return d === 1 ? "אתמול" : `לפני ${d} ימים`;
}
const date = (ts) => ts ? new Date(ts * 1000).toLocaleDateString("he-IL") : "—";
const dateTime = (ts) => ts ? new Date(ts * 1000).toLocaleString("he-IL", { dateStyle: "short", timeStyle: "short" }) : "—";
const dayLabel = (d) => `${Number(d.slice(6, 8))}.${Number(d.slice(4, 6))}`;
const num = (n) => Number(n || 0).toLocaleString("he-IL");
const toDateInput = (ts) => ts ? new Date(ts * 1000).toISOString().slice(0, 10) : "";
const fromDateInput = (v) => v ? Math.floor(new Date(`${v}T23:59:59`).getTime() / 1000) : null;
const levelName = (lv) => catalog?.levels.find((l) => l.level === Number(lv))?.name || String(lv);
const option = (id) => catalog?.options.find((o) => o.id === id);
const lines = (text) => text.split(/[\n,]+/).map((s) => s.trim()).filter(Boolean);

function chip(text, kind = "") { return el("span", { class: `chip ${kind}` }, text); }
function statusChip(status) {
  const [k, t] = STATUS[status] || ["", status];
  return el("span", { class: "row gap nowrap" }, el("span", { class: `dot ${k}` }), t);
}
function paymentChip(p) { const [k, t] = PAYMENT[p] || ["", p || "—"]; return chip(t, k); }
function complianceChip(c) {
  const [k, t] = COMPLIANCE[c?.state] || ["", "—"];
  return el("span", { class: "row gap nowrap" }, el("span", { class: `dot ${k === "ok" ? "on" : k === "bad" ? "off" : k === "warn" ? "wait" : ""}` }), t);
}
function matchesFilter(d, f) {
  const c = d.compliance || {};
  switch (f) {
    case "": return true;
    case "offline": return d.status === "active" && !c.online;
    case "vpn_inactive": return d.status === "active" && c.vpn !== "up";
    case "accessibility_off": return c.accessibility === "off";
    case "unmanaged": return d.status === "active" && c.management !== "device_owner";
    case "sync": return c.policy_sync === "pending" || c.policy_sync === "syncing";
    default: return c.state === f;
  }
}
function levelBadge(lv) { return el("span", { class: `level-badge lv${lv}` }, `${lv} · ${levelName(lv)}`); }
function card(title, sub, ...children) {
  return el("section", { class: "card" },
    title ? el("div", { class: "card-head" }, el("div", { class: "grow" }, el("h2", {}, title),
      sub ? el("p", { class: "muted small card-sub" }, sub) : null)) : null, ...children);
}
function kv(pairs) {
  return el("dl", { class: "kv" }, pairs.filter(Boolean).flatMap(([k, v]) => [el("dt", {}, k), el("dd", {}, v)]));
}
function telLink(phone) { return phone ? el("a", { href: `tel:${phone}`, class: "ltr" }, phone) : "—"; }
function mailLink(email) { return email ? el("a", { href: `mailto:${email}`, class: "ltr" }, email) : "—"; }
function loading() { return el("div", { class: "loading" }, el("span", { class: "spinner" })); }
function empty(text) { return el("p", { class: "empty" }, text); }

function dialog(title, body, okLabel = "אישור", danger = false) {
  const d = $("#dialog");
  $("#dialog-title").textContent = title;
  $("#dialog-body").replaceChildren(...[body].flat().filter(Boolean));
  const ok = $("#dialog-ok");
  ok.textContent = okLabel;
  ok.className = danger ? "danger" : "primary";
  d.returnValue = "";
  d.showModal();
  return new Promise((resolve) => d.addEventListener("close", () => resolve(d.returnValue === "ok"), { once: true }));
}
const confirmDialog = (title, text, okLabel, danger) => dialog(title, el("p", {}, text), okLabel, danger);

// ---------------------------------------------------------------- רכיבים

function levelPicker(current, onPick, includeOff = false) {
  const box = el("div", { class: "levels", role: "radiogroup" });
  const render = (value) => box.replaceChildren(...catalog.levels.filter((l) => includeOff || l.level > 0).map((l) =>
    el("button", {
      type: "button", class: `level-card${l.level === value ? " selected" : ""}`, role: "radio",
      "aria-checked": String(l.level === value),
      onclick: async () => {
        if (l.level === value) return;
        const ok = await onPick(l.level);
        if (ok !== false) render(l.level);
      },
    }, el("span", { class: "num" }, `רמה ${l.level}`), el("strong", { class: `lv${l.level}` }, l.name),
    el("span", { class: "small" }, l.short), el("span", { class: "muted small" }, l.desc))));
  render(Number(current));
  return box;
}

/** עורך החסימות: קטגוריות → מתגים. onChange(set, id, on) — אם מחזיר Promise שנכשל, המתג חוזר. */
function policyEditor(selected, { onChange, requested = new Set(), openAll = false } = {}) {
  const root = el("div", { class: "policy" });
  for (const cat of catalog.categories) {
    const opts = catalog.options.filter((o) => o.category === cat.id);
    const count = el("span", { class: "count" });
    const updateCount = () => {
      const n = opts.filter((o) => selected.has(o.id)).length;
      count.textContent = n ? `${n} פעילות מתוך ${opts.length}` : `${opts.length} אפשרויות`;
    };
    const inputs = new Map();
    const toggle = async (o, on, input) => {
      if (on) selected.add(o.id); else selected.delete(o.id);
      updateCount();
      try {
        await onChange?.(selected, o.id, on);
      } catch (_) {
        if (on) selected.delete(o.id); else selected.add(o.id);
        input.checked = !on;
        updateCount();
      }
    };
    const rows = opts.map((o) => {
      const input = el("input", { type: "checkbox", checked: selected.has(o.id), "aria-label": o.title,
        onchange: (e) => toggle(o, e.target.checked, e.target) });
      inputs.set(o.id, input);
      return el("div", { class: `opt${requested.has(o.id) ? " requested" : ""}` },
        el("label", { class: "switch" }, input, el("span")),
        el("div", { class: "txt" },
          el("div", { class: "title" }, o.title,
            o.popular ? chip("מבוקש", "warn") : null,
            requested.has(o.id) ? chip("הלקוח ביקש", "violet") : null,
            ...(o.mechanisms || []).map((m) => chip(...[MECH[m]?.[1] || m, MECH[m]?.[0]]))),
          o.desc ? el("div", { class: "desc" }, o.desc) : null,
          o.warn ? el("div", { class: "warn-text" }, `⚠ ${o.warn}`) : null,
          o.needs_guard ? el("div", { class: "desc" }, "נאכף בטלפון — דורש שמגן FilterPhone פעיל במכשיר.") : null));
    });
    const setAll = async (on) => {
      for (const o of opts) {
        if (selected.has(o.id) !== on) { if (on) selected.add(o.id); else selected.delete(o.id); inputs.get(o.id).checked = on; }
      }
      updateCount();
      try { await onChange?.(selected, null, on); } catch (_) { /* הממשק יתרענן */ }
    };
    const box = el("section", { class: `cat${openAll || opts.some((o) => selected.has(o.id) || requested.has(o.id)) ? " open" : ""}` },
      el("button", { type: "button", class: "cat-head", onclick: () => box.classList.toggle("open") },
        el("h3", {}, cat.title), count),
      el("div", { class: "cat-body" },
        el("p", { class: "muted small" }, cat.desc),
        el("div", { class: "cat-tools" },
          el("button", { type: "button", class: "ghost small", onclick: () => setAll(true) }, "הפעל הכול"),
          el("button", { type: "button", class: "ghost small", onclick: () => setAll(false) }, "כבה הכול")),
        rows));
    updateCount();
    root.append(box);
  }
  return root;
}

function statsBars(days) {
  const max = Math.max(1, ...days.map((d) => (d.block || 0) + (d.replace || 0) + (d.site || 0)));
  const pct = (v) => `${Math.round(((v || 0) / max) * 100)}%`;
  const bars = el("div", { class: "bars" }, days.map((d) => {
    const bar = el("div", { class: "bar", title: `${dayLabel(d.day)}: ${num(d.block)} נחסמו, ${num(d.replace)} נצבעו, ${num(d.site)} אתרים` });
    for (const [cls, v] of [["b", d.block], ["r", d.replace], ["s", d.site]]) {
      const seg = el("i", { class: cls });
      seg.style.height = pct(v);           // CSSOM — מותר ב-CSP (לא style מוטמע)
      bar.append(seg);
    }
    return bar;
  }));
  return el("div", { class: "stack" }, bars,
    el("div", { class: "bar-labels" }, days.map((d) => el("span", {}, dayLabel(d.day)))),
    el("div", { class: "legend" }, el("span", {}, el("i", { class: "b" }), "תמונות נחסמו"),
      el("span", {}, el("i", { class: "r" }), "תמונות נצבעו"), el("span", {}, el("i", { class: "s" }), "אתרים/שירותים חסומים")));
}

function meter(value, hot) {
  const m = el("div", { class: `meter${hot ? " hot" : ""}` }, el("i"));
  m.firstChild.style.width = `${Math.round(Math.min(1, Math.max(0, value)) * 100)}%`;
  return m;
}

function eventsTable(list, showDevice = true) {
  if (!list.length) return empty("אין עדיין חסימות.");
  return el("div", { class: "table-wrap" }, el("table", {},
    el("thead", {}, el("tr", {}, el("th", {}, "זמן"), showDevice ? el("th", {}, "מכשיר") : null, el("th", {}, "אתר"),
      el("th", {}, "סוג"), el("th", {}, "פעולה"), el("th", {}, "סיבה"))),
    el("tbody", {}, list.map((e) => el("tr", {},
      el("td", { class: "nowrap" }, dateTime(Number(e.ts))),
      showDevice ? el("td", {}, e.device_id ? el("a", { href: `#/devices/${e.device_id}` }, e.device) : e.device) : null,
      el("td", {}, el("span", { class: "ltr" }, e.host), e.path ? el("small", { class: "ltr mono" }, e.path) : null),
      el("td", {}, KINDS[e.kind] || e.kind),
      el("td", {}, chip(ACTIONS[e.action] || e.action, e.action === "replace" ? "warn" : e.action === "site" ? "violet" : "bad")),
      el("td", { class: "ltr mono" }, e.reason))))));
}

// ---------------------------------------------------------------- התחברות

function showLogin() {
  token = "";
  sessionStorage.removeItem("fp_token");
  clearInterval(refreshTimer);
  $("#app").hidden = true;
  $("#login").hidden = false;
}

$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  $("#login-error").textContent = "";
  try {
    const r = await api("/v1/admin/login", { method: "POST", body: { username: f.get("username"), password: f.get("password") } });
    token = r.token;
    sessionStorage.setItem("fp_token", token);
    e.target.reset();
    start();
  } catch (err) {
    $("#login-error").textContent = err.message;
  }
});

$("#logout").addEventListener("click", async () => {
  try { await api("/v1/admin/logout", { method: "POST" }); } catch (_) { /* ממילא יוצאים */ }
  showLogin();
});
$("#menu").addEventListener("click", () => $("#side").classList.toggle("open"));

// ---------------------------------------------------------------- ניתוב

const ROUTES = {
  overview: ["סקירה", viewOverview],
  requests: ["בקשות הצטרפות ושינוי", viewRequests],
  support: ["תמיכה", viewSupport],
  wabot: ["בוט וואטסאפ", viewWabot],
  devices: ["מכשירים", viewDevices],
  customers: ["לקוחות", viewCustomers],
  profiles: ["פרופילים מוכנים", viewProfiles],
  apps: ["חנות אפליקציות", viewApps],
  events: ["חסימות אחרונות", viewEvents],
  test: ["בדיקת תמונה", viewTest],
  codes: ["קודי הפעלה", viewCodes],
  audit: ["יומן פעולות", viewAudit],
  system: ["מערכת", viewSystem],
};

function parseHash() {
  const [path, query] = (location.hash.replace(/^#\/?/, "") || "overview").split("?");
  const [route, arg] = path.split("/");
  return { route: ROUTES[route] ? route : "overview", arg, query: new URLSearchParams(query || "") };
}

let renderSeq = 0;
async function render() {
  const { route, arg, query } = parseHash();
  const seq = ++renderSeq;
  clearInterval(refreshTimer);
  document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.route === route));
  $("#side").classList.remove("open");
  const [title, fn] = ROUTES[route];
  $("#title").textContent = title;
  $("#top-actions").replaceChildren();
  const view = $("#view");
  view.replaceChildren(loading());
  try {
    const node = await fn(arg, query);
    if (seq !== renderSeq) return;          // בינתיים עברו לעמוד אחר
    view.replaceChildren(...[node].flat().filter((n) => n !== null && n !== undefined && n !== false));
  } catch (e) {
    if (seq === renderSeq) view.replaceChildren(card("שגיאה", null, el("p", { class: "error" }, e.message)));
  }
  updateBadge();
}

async function updateBadge() {
  try {
    const o = await api("/v1/admin/overview");
    const n = (o.pending.join || 0) + (o.pending.change || 0);
    const b = $("#badge-requests");
    b.hidden = !n;
    b.textContent = n;
    const sb = $("#badge-support");
    sb.hidden = !o.support_unread;
    sb.textContent = o.support_unread || "";
    $("#whoami").textContent = o.admin;
  } catch (_) { /* לא קריטי */ }
}

function autoRefresh(fn, ms = 15000) {
  clearInterval(refreshTimer);
  refreshTimer = setInterval(() => { if (!document.hidden && !$("#dialog").open) fn(); }, ms);
}

function topAction(label, onclick, cls = "ghost") {
  $("#top-actions").append(el("button", { class: cls, onclick }, label));
}

window.addEventListener("hashchange", render);
document.addEventListener("visibilitychange", () => { if (!document.hidden && token) updateBadge(); });

// ---------------------------------------------------------------- סקירה

async function viewOverview() {
  const o = await api("/v1/admin/overview");
  const pendingN = (o.pending.join || 0) + (o.pending.change || 0);
  const t = o.today;
  const reqs = pendingN ? await api("/v1/admin/requests") : [];
  autoRefresh(render, 30000);
  topAction("רענון", render);
  const tile = (value, label, cls = "", href) => el("div", {
    class: `tile${cls ? ` ${cls}` : ""}${href ? " link" : ""}`, onclick: href ? () => { location.hash = href; } : null,
  }, el("b", {}, num(value)), el("span", {}, label));
  return [
    el("div", { class: "tiles" },
      tile(pendingN, "בקשות ממתינות", pendingN ? "alert" : "", "#/requests"),
      tile(o.active, "מכשירים פעילים", "accent", "#/devices"),
      tile(o.online, "מחוברים עכשיו"),
      tile(o.compliance?.red || 0, "ההגנה לא פועלת", o.compliance?.red ? "alert" : "", "#/devices?state=red"),
      tile(o.compliance?.yellow || 0, "הגנה חלקית", "", "#/devices?state=yellow"),
      tile(t.scanned || 0, "תמונות נבדקו היום"),
      tile(t.block || 0, "נחסמו היום", "", "#/events?action=block"),
      tile(t.replace || 0, "נצבעו היום", "", "#/events?action=replace"),
      tile(t.site || 0, "אתרים נחסמו היום", "", "#/events?kind=host"),
      tile(o.expiring, "מנויים שמסתיימים השבוע", o.expiring ? "alert" : "", "#/customers")),
    el("div", { class: "grid two" },
      card("השבוע", "תמונות ואתרים שנחסמו בכל המכשירים", statsBars(o.week)),
      card("בקשות שמחכות לך", pendingN ? "לחצו לפתיחה ולאישור" : null,
        reqs.length ? el("div", { class: "stack" }, reqs.slice(0, 6).map((r) => el("a", { href: "#/requests", class: "row gap" },
          el("span", { class: "avatar" }, (r.customer_name || "?").slice(0, 1)),
          el("span", { class: "grow" }, el("b", {}, r.customer_name || r.device_name),
            el("small", { class: "sub" }, `${r.kind === "join" ? "הצטרפות" : "בקשת שינוי"} · ${ago(r.created_at)} · רמה ${r.level || "—"}`)),
          paymentChip(r.payment))))
          : empty("אין בקשות פתוחות 🎉"))),
    el("div", { class: "grid two" },
      card("מצב המערכת", null, kv([
        ["מנוע סינון", o.engine ? chip("פעיל", "ok") : chip("לא זמין", "bad")],
        ["חסימות DNS (AdGuard)", !o.adguard.enabled ? chip("לא מוגדר — רק הפרוקסי אוכף", "warn")
          : o.adguard.error ? chip(`שגיאה: ${o.adguard.error}`, "bad")
            : chip(`מסונכרן · ${o.adguard.rules} כללים · ${ago(o.adguard.last_ok)}`, "ok")],
        ["מכשירים רשומים", num(o.devices)],
      ])),
      card("קיצורי דרך", null, el("div", { class: "row gap" },
        el("a", { href: "#/test" }, el("button", { class: "ghost" }, "בדיקת תמונה")),
        el("a", { href: "#/codes" }, el("button", { class: "ghost" }, "קוד הפעלה חדש")),
        el("a", { href: "#/events" }, el("button", { class: "ghost" }, "חסימות אחרונות")),
        el("a", { href: "#/system" }, el("button", { class: "ghost" }, "קטלוג החסימות"))))),
  ];
}

// ---------------------------------------------------------------- בקשות

async function viewRequests(_, query) {
  const status = query.get("status") || "pending";
  const list = await api(`/v1/admin/requests?status=${status}`);
  const tabs = el("div", { class: "row gap" },
    [["pending", "ממתינות"], ["approved", "אושרו"], ["rejected", "נדחו"], ["all", "הכול"]].map(([k, t]) =>
      el("a", { href: `#/requests?status=${k}` }, el("button", { class: k === status ? "primary small" : "ghost small" }, t))));
  const swapStatus = status === "all" ? "pending" : status;
  const { swaps } = await api(`/v1/admin/swaps?status=${swapStatus}`);
  const swapCards = swaps.map((s) => swapCard(s));
  if (!list.length && !swapCards.length) {
    return [tabs, card(null, null, empty(status === "pending" ? "אין בקשות שמחכות לאישור." : "אין בקשות."))];
  }
  return [tabs, ...swapCards, ...list.map((r) => requestCard(r))];
}

/** בקשת החלפת מכשיר / מכשיר נוסף — מהטלפון החדש, אחרי שחזור לפי מספר טלפון. */
function swapCard(s) {
  const pending = s.status === "pending";
  const act = async (body, msg) => {
    await attempt(() => api(`/v1/admin/swaps/${s.id}`, { method: "POST", body }), msg);
    render();
  };
  const isSwap = s.mode === "swap";
  return card(isSwap ? "החלפת מכשיר" : "מכשיר נוסף לחשבון קיים",
    isSwap ? "הלקוח התקין את FilterPhone בטלפון אחר וביקש להעביר אליו את ההגנה. באישור — הטלפון החדש מקבל את " +
      "הרמה והחסימות, והישן מתנתק." : "הלקוח מבקש להגן על מכשיר נוסף באותו חשבון (חשבון נוסף בתשלום).",
    kv([
      ["לקוח", el("span", { class: "row gap" }, s.customer_name, telLink(s.customer_phone))],
      ["טלפון חדש", s.new_name || "—"],
      isSwap ? ["טלפון קודם", el("span", { class: "row gap" }, s.old_name || "—",
        s.old_status ? chip(s.old_status === "active" ? `פעיל · ${ago(s.old_last_seen)}` : "מנותק", s.old_status === "active" ? "warn" : "") : null)] : null,
      ["נשלחה", dateTime(s.created_at)],
      !pending ? ["סטטוס", chip(s.status === "approved" ? "אושרה" : "נדחתה", s.status === "approved" ? "ok" : "bad")] : null,
    ]),
    pending ? el("div", { class: "row gap wrap end" },
      el("button", { class: "primary", onclick: () => act({ approve: true }, isSwap ? "ההגנה הועברה לטלפון החדש" : "המכשיר הנוסף אושר") },
        isSwap ? "אישור החלפה" : "אישור מכשיר נוסף"),
      isSwap ? el("button", { class: "ghost", onclick: () => act({ approve: true, as_additional: true }, "אושר כמכשיר נוסף") },
        "לאשר כמכשיר נוסף (הישן נשאר)") : null,
      el("button", { class: "danger", onclick: async () => {
        if (!await confirmDialog("לדחות את הבקשה?", "הטלפון החדש לא יקבל הגנה.", "לדחות", true)) return;
        await act({ approve: false }, "הבקשה נדחתה");
      } }, "דחייה")) : null);
}

function requestCard(r) {
  const requested = new Set(r.options.map((o) => o.id));
  const pending = r.status === "pending";
  const selected = new Set(r.kind === "change" ? (r.options.length ? r.options.map((o) => o.id) : r.current?.options || [])
    : requested);
  let level = r.level || r.device_level || 1;
  const custom = el("textarea", { rows: 3, placeholder: "example.co.il\nanother-site.com", class: "ltr" });
  const payment = el("select", {}, Object.entries(PAYMENT).map(([k, [, t]]) => el("option", { value: k, selected: (r.payment || "unpaid") === k }, t)));
  const until = el("input", { type: "date", value: toDateInput(r.paid_until) });
  const note = el("input", { placeholder: "הערה פנימית / סיבת דחייה (לא חובה)" });

  const approve = async () => {
    if (payment.value === "unpaid") {
      const go = await confirmDialog("התשלום לא סומן", "הבקשה מסומנת 'לא שולם'. לאשר בכל זאת?", "לאשר");
      if (!go) return;
    }
    await attempt(() => api(`/v1/admin/requests/${r.id}/approve`, { method: "POST", body: {
      level, options: [...selected], custom_hosts: lines(custom.value), payment: payment.value,
      paid_until: fromDateInput(until.value), admin_note: note.value,
    } }), r.kind === "join" ? "אושר — החיבור נפתח. הטלפון יתחבר בתוך דקה." : "השינוי אושר והוחל");
    render();
  };
  const reject = async () => {
    if (!await confirmDialog("לדחות את הבקשה?", note.value ? `סיבה: ${note.value}` : "אפשר לכתוב סיבה בשדה ההערה — הלקוח יראה אותה.", "לדחות", true)) return;
    await attempt(() => api(`/v1/admin/requests/${r.id}/reject`, { method: "POST", body: { admin_note: note.value } }), "הבקשה נדחתה");
    render();
  };

  const head = el("div", { class: "req-top" },
    el("span", { class: "avatar" }, (r.customer_name || "?").slice(0, 1)),
    el("div", { class: "grow stack tight" },
      el("div", { class: "row gap" }, el("h2", {}, r.customer_name || r.device_name || "—"),
        chip(r.kind === "join" ? "בקשת הצטרפות" : "בקשת שינוי", r.kind === "join" ? "accent" : "violet"),
        r.status !== "pending" ? chip({ approved: "אושרה", rejected: "נדחתה", cancelled: "בוטלה" }[r.status] || r.status,
          r.status === "approved" ? "ok" : "bad") : null,
        r.customer_devices > 1 ? chip(`לקוח קיים · ${r.customer_devices} מכשירים`, "warn") : null),
      el("p", { class: "muted small" }, `${dateTime(r.created_at)} · ${ago(r.created_at)}`)),
    paymentChip(r.payment));

  const info = kv([
    ["טלפון", telLink(r.phone)],
    ["מייל", mailLink(r.email)],
    ["מכשיר", `${r.device_name || "—"} · ${r.platform || ""} ${r.app_version ? `(גרסה ${r.app_version})` : ""}`],
    ["רמה שביקש", r.level ? levelBadge(r.level) : "ללא שינוי"],
    ["חסימות שסימן", r.options.length ? el("div", { class: "chips" }, r.options.map((o) => chip(o.title, "violet"))) : "—"],
    r.kind === "change" && r.current ? ["חסימות היום", el("div", { class: "chips" },
      r.current.options.map((id) => chip(option(id)?.title || id)))] : null,
    r.admin_note ? ["הערת מנהל", r.admin_note] : null,
  ]);

  const parts = [head, info];
  if (r.note) parts.push(el("div", { class: "stack tight" }, el("b", { class: "small" }, "מה עוד הלקוח ביקש:"), el("div", { class: "note" }, r.note)));
  if (pending) {
    parts.push(el("div", { class: "sep" }),
      el("h3", {}, "ההחלטה שלך"),
      el("p", { class: "muted small" }, "רק אתה קובע את הרמה והחסימות. מסומן מראש מה שהלקוח ביקש — אפשר לשנות לפני האישור."),
      levelPicker(level, (lv) => { level = lv; }),
      el("details", { class: "more" }, el("summary", {}, `חסימות: ${selected.size} מסומנות — לחצו לעריכה`),
        policyEditor(selected, { requested })),
      el("label", {}, "אתרים נוספים לחסימה (שורה לכל אתר)", custom),
      el("div", { class: "grid three" },
        el("label", {}, "תשלום", payment),
        el("label", {}, "בתוקף עד", until),
        el("label", {}, "הערה", note)),
      el("div", { class: "row end gap" },
        el("button", { class: "danger", onclick: reject }, "דחייה"),
        el("button", { class: "ok", onclick: approve }, r.kind === "join" ? "אישור ופתיחת החיבור" : "אישור השינוי")));
  } else if (r.device_id) {
    parts.push(el("div", { class: "row end" }, el("a", { href: `#/devices/${r.device_id}` }, "לדף המכשיר ›")));
  }
  return el("section", { class: "card req" }, parts);
}

// ---------------------------------------------------------------- מכשירים

async function viewDevices(arg, query) {
  if (arg) return viewDevice(Number(arg));
  const list = await api("/v1/admin/devices");
  const customerFilter = query.get("customer");
  const search = el("input", { type: "search", placeholder: "חיפוש לפי שם, טלפון, כתובת..." });
  const status = el("select", {}, el("option", { value: "" }, "כל המצבים"),
    Object.entries(STATUS).map(([k, [, t]]) => el("option", { value: k }, t)));
  const level = el("select", {}, el("option", { value: "" }, "כל הרמות"),
    catalog.levels.map((l) => el("option", { value: l.level }, `${l.level} · ${l.name}`)));
  const health = el("select", { "aria-label": "מצב הגנה" }, Object.entries(DEVICE_FILTERS).map(([k, t]) =>
    el("option", { value: k, selected: k === (query.get("state") || "") }, t)));
  const body = el("tbody");
  const rank = { red: 0, yellow: 1, green: 2, inactive: 3 };
  const draw = () => {
    const q = search.value.trim().toLowerCase();
    const rows = list.filter((d) => (!customerFilter || String(d.customer_id) === customerFilter)
      && (!status.value || d.status === status.value) && (!level.value || String(d.level) === level.value)
      && matchesFilter(d, health.value)
      && (!q || [d.name, d.label, d.vpn_ip, d.customer?.name, d.customer?.phone].some((x) => (x || "").toLowerCase().includes(q))))
      .sort((a, b) => (rank[a.compliance?.state] ?? 9) - (rank[b.compliance?.state] ?? 9));
    body.replaceChildren(...rows.map((d) => el("tr", { class: "click", onclick: () => { location.hash = `#/devices/${d.id}`; } },
      el("td", {}, el("b", {}, d.name), el("small", {}, [d.customer?.name, d.customer?.phone, d.vpn_ip].filter(Boolean).join(" · "))),
      el("td", {}, d.status === "active" ? el("div", { class: "stack tight" }, complianceChip(d.compliance),
        el("small", {}, [d.compliance.online ? "מחובר" : "לא מחובר",
          d.compliance.issues.length ? d.compliance.issues[0].text : null].filter(Boolean).join(" · ")))
        : statusChip(d.status)),
      el("td", {}, levelBadge(d.level)),
      el("td", {}, d.blocks.length ? el("div", { class: "chips" }, d.blocks.slice(0, 4).map((b) => chip(b.title)),
        d.blocks.length > 4 ? chip(`+${d.blocks.length - 4}`) : null) : el("span", { class: "muted" }, "—")),
      el("td", { class: "nowrap" }, `${num(d.today.block)} / ${num(d.today.replace)}`, el("small", {}, "נחסמו / נצבעו")),
      el("td", { class: "nowrap" }, ago(d.last_handshake || d.last_seen_at)),
      el("td", {}, d.customer ? paymentChip(d.customer.payment) : null))));
    if (!rows.length) body.replaceChildren(el("tr", {}, el("td", { colspan: 7 }, empty("לא נמצאו מכשירים."))));
  };
  [search, status, level, health].forEach((x) => x.addEventListener("input", draw));
  draw();
  autoRefresh(async () => { const fresh = await api("/v1/admin/devices"); list.splice(0, list.length, ...fresh); draw(); }, 20000);
  topAction("רענון", render);
  const counts = list.reduce((m, d) => { m[d.compliance?.state] = (m[d.compliance?.state] || 0) + 1; return m; }, {});
  return [
    el("div", { class: "row gap" }, ["red", "yellow", "green"].map((k) => el("button", {
      class: `ghost small${health.value === k ? " selected" : ""}`, onclick: () => { health.value = k; draw(); },
    }, complianceChip({ state: k }), ` ${counts[k] || 0}`))),
    el("div", { class: "row gap" }, el("div", { class: "grow" }, search), health, status, level,
      customerFilter ? el("a", { href: "#/devices" }, el("button", { class: "ghost small" }, "הצג את כל הלקוחות")) : null),
    el("section", { class: "card" }, el("div", { class: "table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {}, el("th", {}, "מכשיר"), el("th", {}, "הגנה"), el("th", {}, "רמה"), el("th", {}, "חסימות"),
        el("th", {}, "היום"), el("th", {}, "נראה לאחרונה"), el("th", {}, "תשלום"))), body))),
  ];
}

async function viewDevice(id) {
  const d = await api(`/v1/admin/devices/${id}`);
  $("#title").textContent = d.name;
  const patch = async (body, msg = "נשמר ✓") => {
    const fresh = await attempt(() => api(`/v1/admin/devices/${id}`, { method: "PATCH", body }), msg);
    Object.assign(d, fresh);
    return fresh;
  };
  const back = el("a", { href: "#/devices", class: "row gap" }, el("span", { class: "ico ico-back" }), "כל המכשירים");
  const active = d.status === "active";
  topAction("שיחת תמיכה", () => { location.hash = `#/support/${id}`; });

  // --- כותרת
  const header = el("section", { class: "card" },
    el("div", { class: "req-top" },
      el("span", { class: "avatar" }, d.name.slice(0, 1)),
      el("div", { class: "grow stack tight" },
        el("div", { class: "row gap" }, el("h2", {}, d.name), statusChip(d.status),
          active ? complianceChip(d.compliance) : null,
          active ? el("span", { class: "row gap nowrap small" }, el("span", { class: `dot ${d.compliance.online ? "on" : ""}` }),
            d.compliance.online ? "מחובר עכשיו" : `לא מחובר · ${ago(d.compliance.last_contact)}`) : null),
        el("p", { class: "muted small" }, [d.platform, d.app_version && `גרסה ${d.app_version}`, d.vpn_ip, `נרשם ${date(d.created_at)}`].filter(Boolean).join(" · "))),
      el("div", { class: "actions" },
        el("button", { class: "ghost small", onclick: async () => {
          const input = el("input", { value: d.name });
          if (await dialog("שם המכשיר", el("label", {}, "שם", input), "שמירה")) { await patch({ name: input.value }); render(); }
        } }, "שינוי שם"),
        active || d.status === "disabled" ? el("button", { class: "ghost small", onclick: async () => {
          const turnOff = d.status === "active";
          if (turnOff && !await confirmDialog("להשבית את המכשיר?", "החיבור לסינון יתנתק. אם בטלפון מופעל 'חסימת חיבורים ללא VPN' — לא תהיה בו גלישה בכלל.", "השבתה", true)) return;
          await patch({ status: turnOff ? "disabled" : "active" }, turnOff ? "המכשיר הושבת" : "המכשיר הופעל");
          render();
        } }, d.status === "active" ? "השבתה" : "הפעלה") : null,
        el("button", { class: "danger small", onclick: async () => {
          if (!await confirmDialog(`למחוק את "${d.name}"?`, "המכשיר יתנתק ויצטרך הרשמה או קוד חדש. לא ניתן לבטל.", "מחיקה", true)) return;
          await attempt(() => api(`/v1/admin/devices/${id}`, { method: "DELETE" }), "נמחק");
          location.hash = "#/devices";
        } }, "מחיקה"))));

  if (d.status === "pending") {
    return [back, header, card("ממתין לאישור", null, el("p", {}, "המכשיר עוד לא אושר. האישור נעשה מדף הבקשות."),
      el("div", { class: "row" }, el("a", { href: "#/requests" }, el("button", { class: "primary" }, "לבקשות"))))];
  }

  // --- לקוח
  const c = d.customer;
  const customerCard = card("לקוח", null, c ? el("div", { class: "stack" }, kv([
    ["שם", c.name], ["טלפון", telLink(c.phone)], ["מייל", mailLink(c.email)],
    ["תשלום", el("span", { class: "row gap" }, paymentChip(c.payment), c.paid_until ? `עד ${date(c.paid_until)}` : null)],
    c.notes ? ["הערות", c.notes] : null,
  ]), el("div", { class: "row" }, el("button", { class: "ghost small", onclick: () => editCustomer(c).then((ok) => ok && render()) }, "עריכת פרטי לקוח"),
    el("a", { href: `#/devices?customer=${c.id}` }, "כל המכשירים של הלקוח ›")))
    : el("p", { class: "muted" }, d.label ? `הופעל בקוד: ${d.label}` : "מכשיר שהופעל בקוד, בלי פרטי לקוח."));

  // --- רמה
  const levelCard = card("רמת סינון תמונות", "השינוי חל תוך כמה שניות. רק כאן אפשר לשנות — הלקוח לא יכול.",
    levelPicker(d.level, async (lv) => {
      try { await patch({ level: lv }, `הרמה שונתה ל${levelName(lv)}`); return true; } catch (_) { return false; }
    }, true));

  // --- חסימות
  const selected = new Set(d.settings.options);
  const saveState = el("span", { class: "save-state" });
  const summaryLine = () => `${selected.size} אפשרויות פעילות · ${d.compiled.hosts} אתרים חסומים ברשת · ${d.compiled.apps} אפליקציות חסומות בטלפון`;
  const summaryEl = el("p", { class: "muted small" }, summaryLine());
  let pendingSave = Promise.resolve();
  const policyCard = card("חסימות", "כל מתג נשמר מיד. 'רשת' = נחסם בשרת (DNS + פרוקסי), 'בטלפון' = נחסם ע\"י מגן FilterPhone בטלפון.",
    summaryEl,
    d.compiled.needs_guard && !d.guard?.accessibility && !d.guard?.device_owner
      ? el("p", { class: "warn-text" }, "⚠ חלק מהחסימות נאכפות בטלפון — ומגן FilterPhone לא דיווח שהוא פעיל במכשיר הזה.") : null,
    policyEditor(selected, { onChange: (set) => {
      saveState.textContent = "שומר…";
      pendingSave = pendingSave.then(() => patch({ options: [...set] }, null)).then(() => {
        saveState.textContent = "נשמר ✓"; summaryEl.textContent = summaryLine();
      }).catch((e) => { saveState.textContent = ""; throw e; });
      return pendingSave;
    } }), saveState);

  // --- תמונות באפליקציות
  const appImages = el("div", { class: "levels" }, Object.entries(APP_IMAGES).map(([k, [t, sub]]) => el("button", {
    type: "button", class: `level-card${d.settings.app_images === k ? " selected" : ""}`,
    onclick: async (e) => {
      await patch({ app_images: k });
      appImages.querySelectorAll(".level-card").forEach((b) => b.classList.remove("selected"));
      e.currentTarget.classList.add("selected");
    },
  }, el("strong", {}, t), el("span", { class: "muted small" }, sub))));
  const appImagesCard = card("תמונות באפליקציות שאי אפשר לסנן", `כרגע בפועל: ${APP_IMAGES[d.compiled.app_images][0]}. רוב האפליקציות באנדרואיד לא נותנות לבדוק תמונות — זו ההחלטה מה קורה בהן.`, appImages);

  // --- רשימות ידניות
  const ta = (key, ph) => el("textarea", { class: "ltr", rows: 4, placeholder: ph, value: (d.settings[key] || []).join("\n") });
  const hosts = ta("custom_hosts", "example.co.il");
  const allow = ta("allow_hosts", "kosher-site.co.il");
  const customCard = card("אתרים — ידני", "חסימה של כל אתר שלא ברשימה, ורשימת היתר (מסירה חסימה מאתר מסוים). אפליקציות — בכרטיס 'האפליקציות בטלפון'.",
    el("div", { class: "grid two" },
      el("label", {}, "אתרים לחסימה", hosts),
      el("label", {}, "אתרים מותרים (חריגים)", allow)),
    el("div", { class: "row end" }, el("button", { class: "primary", onclick: async () => {
      await patch({ custom_hosts: lines(hosts.value), allow_hosts: lines(allow.value) });
      render();
    } }, "שמירה")));

  // --- האפליקציות בטלפון
  const appsCard = phoneAppsCard(d, patch);

  // --- מצב ההגנה בטלפון (תאימות)
  const complianceCardEl = complianceCard(d);
  const logCard = deviceLogCard(d);
  const dpcCardEl = dpcCard(d, patch);
  const keywordCardEl = keywordCard(d, patch);
  const bypassCardEl = vpnBypassCard(d, patch);
  const maintenanceCardEl = maintenanceCard(d);

  // --- סטטיסטיקה ואירועים
  const statsCard = card("7 הימים האחרונים", null, statsBars(d.history));
  const eventsCard = card("חסימות אחרונות במכשיר", null, eventsTable(d.events, false),
    el("div", { class: "row end" }, el("a", { href: `#/events?device=${id}` }, "לכל החסימות ›")));
  const learned = d.learned || [];
  const learnedCard = card("אפליקציות שעוברות בלי בדיקה", "אפליקציות שלא סומכות על תעודת הסינון — המערכת למדה להעביר אותן כדי שיעבדו (DNS עדיין חל).",
    learned.length ? el("div", { class: "chips" }, learned.map((h) => chip(h.host))) : empty("אין."),
    el("div", { class: "row end" }, el("button", { class: "ghost small", onclick: async () => {
      const r = await attempt(() => api(`/v1/admin/devices/${id}/reset-apps`, { method: "POST" }));
      toast(`אופסו ${r.cleared} כתובות`);
      render();
    } }, "איפוס")));
  const historyCard = d.requests.length ? card("היסטוריית בקשות", null, el("div", { class: "table-wrap" }, el("table", {},
    el("tbody", {}, d.requests.map((r) => el("tr", {},
      el("td", { class: "nowrap" }, dateTime(r.created_at)),
      el("td", {}, r.kind === "join" ? "הצטרפות" : "שינוי"),
      el("td", {}, chip({ pending: "ממתינה", approved: "אושרה", rejected: "נדחתה", cancelled: "בוטלה" }[r.status] || r.status)),
      el("td", {}, r.note || ""))))))) : null;

  return [back, header,
    complianceCardEl,
    el("div", { class: "grid two" }, customerCard, logCard),
    maintenanceCardEl,
    levelCard, policyCard, appsCard, appImagesCard, customCard, keywordCardEl, bypassCardEl, dpcCardEl,
    el("div", { class: "grid two" }, statsCard, learnedCard),
    eventsCard, historyCard];
}

function yesNo(v, good = true, unknown = "לא דווח") {
  return v === undefined || v === null ? chip(unknown) : chip(v ? "כן" : "לא", v === good ? "ok" : "bad");
}

function complianceCard(d) {
  const c = d.compliance;
  const hb = d.heartbeat || {};
  const g = d.guard || {};
  const vpn = hb.vpn || {};
  const acc = hb.accessibility || {};
  const tamper = hb.tamper || {};
  const pick = (a, b) => (a === undefined ? b : a);
  const syncText = d.device_policy_version === null || d.device_policy_version === undefined
    ? chip("הטלפון עוד לא אישר גרסה", "warn")
    : d.device_policy_version >= d.policy_version ? chip("מסונכרן", "ok")
      : c.policy_sync === "deferred" ? chip("ייושם בפתיחת האפליקציה / בתחזוקה הלילית", "")
      : chip("Policy synchronization pending — ממתין לטלפון", c.policy_sync === "pending" ? "bad" : "warn");
  const issues = c.issues.length ? el("ul", { class: "issues" }, c.issues.map((i) =>
    el("li", { class: i.severity === "red" ? "bad" : "warn" }, i.text))) : el("p", { class: "ok-text" }, "✓ כל רכיבי ההגנה פועלים.");
  return card("מצב ההגנה בטלפון", d.heartbeat_at ? `דיווח אחרון מהטלפון ${ago(d.heartbeat_at)}` : "הטלפון עוד לא שלח דיווח תקופתי (גרסת אפליקציה ישנה?)",
    el("div", { class: "row gap" }, el("span", { class: `state-badge ${c.state}` }, COMPLIANCE[c.state]?.[1] || c.state)),
    issues,
    el("div", { class: "grid two" },
      kv([
        ["VPN (סינון)", c.vpn === "up" ? chip("פעיל", "ok") : c.vpn === "down" ? chip("כבוי", "bad") : chip("לא ידוע")],
        ["VPN — נראה לאחרונה", ago(d.last_handshake)],
        ["סינון רשת (הדיווח עבר דרך המנהרה)", yesNo(hb.via_vpn)],
        ["VPN תמיד מופעל", yesNo(pick(vpn.always_on, g.always_on))],
        ["חסימת חיבורים בלי VPN", yesNo(pick(vpn.lockdown, g.lockdown))],
        ["בדיקת VPN אחרונה שעברה", ago(vpn.last_health_ok)],
        ["מגן FilterPhone (נגישות)", c.accessibility === "not_required" ? chip("לא נדרש") : c.accessibility === "on"
          ? chip(acc.running === false ? "מופעל אבל לא רץ" : "פעיל", acc.running === false ? "warn" : "ok")
          : c.accessibility === "off" ? chip("כבוי", "bad") : chip("לא דווח")],
        ["החזרה אוטומטית של המגן", acc.self_heal === undefined ? chip("לא דווח")
          : acc.self_heal ? chip(`פעילה${acc.restored ? ` · הוחזר ${num(acc.restored)} פעמים` : ""}`, "ok")
            : chip("לא — דרושה הרשאה בהקמה (root / adb)", "warn")],
        ["חסימות שבוצעו בטלפון", hb.protections ? `${num(hb.protections.apps_blocked)} אפליקציות · ${num(hb.protections.screens_blocked)} מסכים · ${num(hb.protections.wa_profile_photo)} תמונות פרופיל · ${num(hb.protections.keywords_blocked)} מילות חיפוש` : "—"],
      ]),
      kv([
        ["מנהל מכשיר (Device Owner)", yesNo(pick(hb.device_owner, g.device_owner))],
        ["מנהל מכשיר רגיל (Device Admin)", yesNo(pick(hb.device_admin, g.device_admin), true, "לא דווח")],
        ["ניהול / שיבוש", d.managed_at && hb.device_owner === false ? chip("הניהול הוסר!", "bad")
          : tamper.adb ? chip("ADB פעיל", "warn") : tamper.dev_options ? chip("אפשרויות מפתחים פעילות", "warn") : chip("תקין", "ok")],
        ["אופטימיזציית סוללה", pick(hb.battery_unrestricted, g.battery_unrestricted) === undefined ? chip("לא דווח")
          : pick(hb.battery_unrestricted, g.battery_unrestricted) ? chip("ללא הגבלה", "ok") : chip("מוגבלת", "warn")],
        ["תעודת סינון", yesNo(pick(hb.ca_installed, g.ca_installed))],
        ["גרסת אפליקציה", el("span", { class: "row gap" }, d.app_version || hb.app_version || "—",
          d.latest_app_version && (d.app_version || hb.app_version) && d.latest_app_version !== (d.app_version || hb.app_version)
            ? chip(`האחרונה: ${d.latest_app_version}`, "warn") : null)],
        ["עדכון אוטומטי", hb.update?.state && UPDATE_STATES[hb.update.state]
          ? chip(`${UPDATE_STATES[hb.update.state][1]}${hb.update.version ? ` (${hb.update.version})` : ""}${hb.update.error ? ` · ${hb.update.error}` : ""}`,
            UPDATE_STATES[hb.update.state][0])
          : chip(pick(hb.device_owner, g.device_owner) ? "פעיל (מנהל מכשיר)" : "ידני — מדף ההורדה")],
        ["אנדרואיד", hb.sdk ? `${ANDROID[hb.sdk] || "?"} (API ${hb.sdk})` : "—"],
        ["דגם", hb.capabilities ? `${hb.capabilities.manufacturer || ""} ${hb.capabilities.model || ""}`.trim() || "—" : "—"],
        ["יכולות המכשיר", capabilityChips(hb.capabilities)],
        ["אפשרות הקמה כמנהל מכשיר", hb.preflight ? chip((PREFLIGHT[hb.preflight] || ["", hb.preflight])[1], (PREFLIGHT[hb.preflight] || [""])[0]) : "—"],
        ["גרסת מדיניות בשרת / בטלפון", el("span", { class: "row gap" }, `${d.policy_version} / ${d.device_policy_version ?? "—"}`, syncText)],
        ["סנכרון מדיניות אחרון", ago(d.policy_ack_at || hb.last_policy_sync)],
      ])));
}

const PREFLIGHT = {
  ALREADY_MANAGED: ["ok", "מנוהל (Device Owner)"],
  READY_WITH_ROOT: ["ok", "כשיר — אפשר בלחיצה (root)"],
  READY_FOR_PROVISIONING: ["warn", "כשיר — הקמה ב-QR / adb"],
  REQUIRES_FACTORY_RESET: ["warn", "דרוש איפוס יצרן להקמה כמנהל מכשיר"],
  UNSUPPORTED: ["bad", "המכשיר לא נתמך להקמה מלאה"],
};

function capabilityChips(c) {
  if (!c) return "—";
  const items = [
    ["root", "root"], ["provisioning_allowed", "ניתן להקמה"], ["device_owner", "מנהל מכשיר"],
    ["always_on", "VPN תמיד"], ["lockdown", "נעילה בלי VPN"], ["accessibility", "מגן"],
  ];
  return el("span", { class: "row gap wrap" },
    items.map(([k, t]) => chip(t, c[k] ? "ok" : "")));
}

function deviceLogCard(d) {
  const list = d.device_log || [];
  return card("יומן הטלפון", "שינויים במצב ההגנה כפי שדווחו (כיבוי VPN, כיבוי המגן, הסרת ניהול...)",
    list.length ? el("div", { class: "table-wrap" }, el("table", {}, el("tbody", {}, list.map((e) => el("tr", {},
      el("td", { class: "nowrap" }, dateTime(e.ts)),
      el("td", {}, chip((LOG_KINDS[e.kind] || ["", e.kind])[1], (LOG_KINDS[e.kind] || [""])[0])),
      el("td", { class: "small" }, e.detail || "")))))) : empty("אין אירועים."));
}

function dpcCard(d, patch) {
  const eff = d.settings.dpc_effective || {};
  const isDo = d.compliance?.management === "device_owner";
  const applied = new Set(d.heartbeat?.dpc?.applied || []);
  const failed = new Set(d.heartbeat?.dpc?.failed || []);
  const rows = Object.entries(DPC_LABELS).map(([k, [t, sub]]) => {
    const input = el("input", { type: "checkbox", checked: Boolean(eff[k]), "aria-label": t, onchange: async (e) => {
      try { await patch({ dpc: { [k]: e.target.checked } }); } catch (_) { e.target.checked = !e.target.checked; }
    } });
    return el("div", { class: "app-row" }, el("label", { class: "switch" }, input, el("span")),
      el("div", { class: "grow" }, el("b", {}, t), el("small", {}, sub)),
      isDo ? (failed.has(k) ? chip("נכשל בטלפון", "bad") : applied.has(k) ? chip("הוחל", "ok") : null) : null);
  });
  const frp = el("textarea", { class: "ltr", rows: 2, placeholder: "118237465029384756123", value: (eff.frp_accounts || []).join("\n") });
  return card("מנהל מכשיר (Device Owner)", isDo
    ? "המכשיר מנוהל — ההגדרות כאן נאכפות בו ע\"י אנדרואיד עצמו."
    : "ההגדרות נשמרות, אבל נאכפות רק בטלפון שהוקם כמנהל מכשיר (QR בעמוד 'קודי הפעלה'). בטלפון רגיל אין להן השפעה.",
    el("div", { class: "dpc-list" }, rows),
    el("label", {}, "הגנת איפוס (FRP) — מזהי חשבון Google שיכולים לשחרר טלפון שאופס (מספר, לא מייל). ריק = ללא שינוי",
      frp),
    el("p", { class: "muted small" }, "חשוב: אף הגדרה כאן לא הופכת איפוס ליצרן ל'בלתי אפשרי'. איפוס בכפתורי החומרה תמיד אפשרי; אחריו FRP דורש את החשבון שהוגדר. להקמה מחדש אוטומטית אחרי איפוס — זירו-טאץ' / Knox (ראו docs/DEVICE_OWNER.md)."),
    el("div", { class: "row end" }, el("button", { class: "primary", onclick: async () => {
      await patch({ dpc: { frp_accounts: lines(frp.value) } });
    } }, "שמירת FRP")));
}

/** החנות: אפליקציות שהמנהל אישר. העלאת APK = התקנה ועדכון ישירים בטלפונים מנוהלים. */
async function viewApps() {
  const { apps, categories = [] } = await api("/v1/admin/apps");
  // הקטגוריות = הטאבים בחנות שבטלפון. קטגוריה חדשה מופיעה כטאב אחרי הקבועות.
  const catList = el("datalist", { id: "store-cats" }, categories.map((c) => el("option", { value: c })));
  const file = el("input", { type: "file", accept: ".apk,application/vnd.android.package-archive" });
  const progress = el("span", { class: "muted small" });
  const upload = el("button", { class: "primary", onclick: async () => {
    const f = file.files?.[0];
    if (!f) { toast("בחרו קובץ APK", true); return; }
    upload.disabled = true;
    progress.textContent = `מעלה ${(f.size / 1048576).toFixed(1)}MB…`;
    try {
      const res = await fetch("/v1/admin/apps/upload", { method: "PUT", body: f,
        headers: { "Content-Type": "application/vnd.android.package-archive", Authorization: `Bearer ${token}` } });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || `שגיאה ${res.status}`);
      toast(`${data.label || data.package} ${data.version_name || ""} נוספה לחנות`);
      render();
    } catch (e) {
      toast(e.message, true);
    } finally {
      upload.disabled = false;
      progress.textContent = "";
    }
  } }, "העלאה");
  const pkg = el("input", { class: "ltr", placeholder: "com.example.app" });
  const label = el("input", { placeholder: "שם בחנות" });
  const category = el("input", { placeholder: "קטגוריה (טאב בחנות)", list: "store-cats" });
  const playUrl = el("input", { class: "ltr", placeholder: "קישור Google Play (רשות) — https://play.google.com/...",
    oninput: (e) => {
      const m = e.target.value.match(/[?&]id=([\w.]+)/);     // חילוץ שם החבילה מהקישור
      if (m && !pkg.value.trim()) pkg.value = m[1];
    } });
  const addStoreOnly = el("button", { class: "ghost", onclick: async () => {
    await attempt(() => api("/v1/admin/apps", { method: "POST", body: {
      package: pkg.value.trim(), label: label.value, category: category.value, play_url: playUrl.value.trim() } }), "נוספה");
    render();
  } }, "הוספה בלי קובץ");
  const save = (a, changes, msg) => {
    const body = { ...a, ...changes };
    if (typeof body.screenshots === "string") {
      try { body.screenshots = JSON.parse(body.screenshots); } catch { body.screenshots = []; }
    }
    return attempt(() => api(`/v1/admin/apps/${a.id}`, { method: "PATCH", body }), msg).then(render, () => {});
  };
  // חיפוש ברשימה (שם / חבילה / קטגוריה) — בלי טעינה מחדש
  const filter = el("input", { type: "search", placeholder: `חיפוש ב-${apps.length} אפליקציות…`, oninput: (e) => {
    const q = e.target.value.trim().toLowerCase();
    rows.forEach((r, i) => {
      const a = apps[i];
      r.hidden = Boolean(q) && ![a.label, a.package, a.category].some((v) => (v || "").toLowerCase().includes(q));
    });
  } });
  const rows = apps.map((a) => {
    const direct = Boolean(a.apk_url && a.version_code);
    const sw = (key, title, sub) => {
      const input = el("input", { type: "checkbox", checked: Boolean(a[key]), "aria-label": title,
        onchange: (e) => save(a, { [key]: e.target.checked }, "נשמר") });
      return el("label", { class: "row gap nowrap small", title: sub }, el("span", { class: "switch" }, input, el("span")), title);
    };
    return el("div", { class: "app-row" },
      el("div", { class: "grow" }, el("b", {}, a.label || a.package), el("small", { class: "ltr" }, a.package),
        el("small", {}, [a.category, direct ? `גרסה ${a.version_name || a.version_code} · התקנה ישירה`
          : a.play_url ? "התקנה ועדכונים דרך Google Play" : "בלי קובץ — בקשה / התקנה ידנית",
          `עודכן ${date(a.updated_at)}`].filter(Boolean).join(" · "))),
      direct ? chip("APK אצלנו", "ok") : a.play_url ? chip("Google Play", "ok") : chip("ידני"),
      sw("enabled", "מוצגת", "מופיעה בחנות בטלפונים"),
      direct ? sw("required", "חובה", "מותקנת אוטומטית בכל טלפון מנוהל") : null,
      el("button", { class: "ghost small", onclick: async () => {
        const l = el("input", { value: a.label || "" });
        const c = el("input", { value: a.category || "", list: "store-cats" });
        const n = el("textarea", { rows: 2, value: a.note || "" });
        const desc = el("textarea", { rows: 5, value: a.description || "" });
        const icon = el("input", { class: "ltr", value: a.icon_url || "", placeholder: "https://.../icon.png" });
        const play = el("input", { class: "ltr", value: a.play_url || "", placeholder: "https://play.google.com/..." });
        const shots = el("textarea", { rows: 4, class: "ltr",
          value: (() => { try { return JSON.parse(a.screenshots || "[]").join("\n"); } catch { return ""; } })(),
          placeholder: "כתובת צילום מסך בכל שורה (https)" });
        if (!await dialog("עריכת אפליקציה", el("div", { class: "stack" },
          el("label", {}, "שם", l), el("label", {}, "קטגוריה", c), el("label", {}, "תיאור קצר (רשימה)", n),
          el("label", {}, "תיאור מלא (עמוד האפליקציה)", desc), el("label", {}, "אייקון (URL)", icon),
          el("label", {}, "קישור Google Play", play),
          el("label", {}, "צילומי מסך (URL בכל שורה, עד 8)", shots)), "שמירה")) return;
        const screenshots = lines(shots.value).filter((u) => u.startsWith("https://")).slice(0, 8);
        await save(a, { label: l.value, category: c.value, note: n.value, description: desc.value,
          icon_url: icon.value.trim(), play_url: play.value.trim(), screenshots }, "נשמר");
      } }, "עריכה"),
      el("button", { class: "danger small", onclick: async () => {
        if (!await confirmDialog(`להסיר את ${a.label || a.package} מהחנות?`, "הטלפונים לא יציעו אותה יותר. אפליקציה שכבר הותקנה נשארת.", "הסרה", true)) return;
        await attempt(() => api(`/v1/admin/apps/${a.id}`, { method: "DELETE" }), "הוסרה");
        render();
      } }, "הסרה"));
  });
  return [
    card("העלאת אפליקציה", "מעלים קובץ APK — השרת קורא ממנו את שם החבילה והגרסה ובודק אותו. טלפון מנוהל (מנהל מכשיר) " +
      "מתקין ומעדכן ממנו ישירות, גם כשהתקנת אפליקציות חסומה בו. גרסה חדשה של אותה אפליקציה = עדכון אצל כולם.",
      el("div", { class: "row gap wrap" }, file, upload, progress)),
    card("אפליקציה מ-Google Play / בלי קובץ", "מדביקים קישור Google Play — הלקוח מתקין ומעדכן דרך Play (בלי שנארח קובץ). בלי קישור — תופיע כ'בקשה'.",
      el("div", { class: "row gap wrap" }, pkg, label, category, playUrl, addStoreOnly), catList),
    card(`בחנות (${apps.length})`, null, apps.length ? [filter, el("div", { class: "apps" }, rows)] : empty("עוד אין אפליקציות בחנות.")),
  ];
}

// ---------------------------------------------------------------- תמיכה

const VIA = { app: "באפליקציה", dashboard: "מהממשק", whatsapp: "מוואטסאפ" };

function diagText(d) {
  if (!d) return "";
  const yes = (v) => (v ? "✓" : "✗");
  return [
    d.vpn !== undefined ? `VPN: ${d.vpn}` : null,
    d.accessibility !== undefined ? `נגישות ${yes(d.accessibility)}` : null,
    d.device_owner !== undefined ? `מנהל מכשיר ${yes(d.device_owner)}` : null,
    d.battery_unrestricted !== undefined ? `סוללה ${d.battery_unrestricted ? "ללא הגבלה" : "מוגבלת"}` : null,
    d.paused ? "בהשהיה" : null,
    d.app_version ? `גרסה ${d.app_version}` : null,
    d.sdk ? `Android ${ANDROID[d.sdk] || d.sdk}` : null,
    d.topic ? `נושא: ${d.topic}` : null,
    d.vpn_error ? `שגיאה: ${d.vpn_error}` : null,
  ].filter(Boolean).join(" · ");
}

function bubble(m) {
  const mine = m.from === "support";
  const meta = [dateTime(m.ts), mine ? VIA[m.via] || m.via : null,
    mine && m.author && m.author !== "whatsapp" ? m.author : null].filter(Boolean).join(" · ");
  return el("div", { class: `bubble ${mine ? "out" : "in"}` },
    el("div", { class: "text" }, m.text),
    !mine && m.diag ? el("div", { class: "diag" }, diagText(m.diag)) : null,
    el("small", { class: "meta" }, meta),
    m.wa_error ? el("small", { class: "error" }, `לא נשלח לוואטסאפ: ${m.wa_error}`) : null);
}

async function viewSupport(arg) {
  if (arg) return viewSupportThread(Number(arg));
  const s = await api("/v1/admin/support");
  autoRefresh(render, 15000);
  topAction("רענון", render);
  const rows = s.threads.map((t) => el("a", { class: `thread-row${t.unread ? " unread" : ""}`, href: `#/support/${t.device_id}` },
    el("span", { class: "avatar" }, (t.customer || t.device_name || "?").slice(0, 1)),
    el("div", { class: "grow" },
      el("div", { class: "row between" }, el("b", {}, t.customer || t.device_name || `מכשיר #${t.device_id}`), el("small", { class: "muted" }, ago(t.last_ts))),
      el("small", { class: "muted" }, `${t.device_name || ""} · #${t.device_id}`),
      el("p", { class: "last" }, (t.last_from === "support" ? "↩ " : "") + t.last_text)),
    t.unread ? el("b", { class: "badge" }, t.unread) : null));
  return [
    s.whatsapp ? null : el("div", { class: "note" }, "פניות מגיעות לכאן. כדי לקבל אותן גם בוואטסאפ שלך ולענות משם — ",
      el("a", { href: "#/system" }, "מערכת ← וואטסאפ"), "."),
    card(`פניות (${s.threads.length})`, s.unread ? `${s.unread} שיחות מחכות לתשובה` : null,
      rows.length ? el("div", { class: "threads" }, rows) : empty("עוד אין פניות. כשלקוח יכתוב בצ'אט באפליקציה — זה יופיע כאן.")),
  ];
}

async function viewSupportThread(id) {
  let t = await api(`/v1/admin/support/${id}`);
  $("#title").textContent = `תמיכה · ${t.device.customer || t.device.name}`;
  topAction("כל הפניות", () => { location.hash = "#/support"; });
  topAction("לדף המכשיר", () => { location.hash = `#/devices/${id}`; });
  const box = el("div", { class: "chat" });
  const paint = () => {
    box.replaceChildren(...(t.messages.length ? t.messages.map(bubble) : [empty("אין הודעות.")]));
    box.scrollTop = box.scrollHeight;
  };
  const text = el("textarea", { rows: 3, maxlength: 2000, placeholder: "תשובה ללקוח… (Ctrl+Enter לשליחה)" });
  const send = el("button", { class: "primary", onclick: async () => {
    const v = text.value.trim();
    if (!v) return;
    send.disabled = true;
    try {
      await attempt(() => api(`/v1/admin/support/${id}`, { method: "POST", body: { text: v } }), "נשלח — יופיע אצל הלקוח באפליקציה");
      text.value = "";
      t = await api(`/v1/admin/support/${id}`);
      paint();
    } catch (_) { /* הודעת השגיאה כבר הוצגה */ } finally {
      send.disabled = false;
    }
  } }, "שליחה");
  text.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) send.click(); });
  // רענון שקט: רק ההודעות, בלי לגעת במה שמוקלד
  autoRefresh(async () => {
    const fresh = await api(`/v1/admin/support/${id}`).catch(() => null);
    if (fresh && fresh.messages.length !== t.messages.length) { t = fresh; paint(); }
  }, 10000);
  paint();
  setTimeout(() => { box.scrollTop = box.scrollHeight; }, 0);
  return card(`${t.device.customer || t.device.name} · מכשיר #${id}`, "התשובה מגיעה לאפליקציה של הלקוח (והתראה בטלפון בדיווח הבא).",
    box, el("div", { class: "composer" }, text, send));
}

async function viewProfiles() {
  catalog = catalog || await api("/v1/admin/catalog");
  const { profiles } = await api("/v1/admin/profiles");
  const list = el("div", { class: "cards" }, profiles.map((p) => el("div", { class: "card profile" },
    el("div", { class: "row between" }, el("b", {}, p.name),
      el("span", { class: "row gap" }, chip(`רמה ${p.level}`, p.level === 1 ? "bad" : p.level === 3 ? "ok" : "warn"),
        p.builtin ? chip("מובנה") : null)),
    el("div", { class: "chips" }, (p.summary || []).slice(0, 12).map((b) => chip(b.title)),
      (p.summary || []).length ? null : el("small", { class: "muted" }, "רק סינון תמונות לפי הרמה")),
    el("div", { class: "row end gap" },
      el("button", { class: "ghost small", onclick: () => openProfileEditor(p) }, "עריכה"),
      p.builtin ? null : el("button", { class: "danger small", onclick: async () => {
        if (!await confirmDialog("למחוק את הפרופיל?", p.name, "מחיקה", true)) return;
        await attempt(() => api(`/v1/admin/profiles/${p.id}`, { method: "DELETE" }), "נמחק");
        render();
      } }, "מחיקה")))));
  return [
    card("פרופילים מוכנים", "חבילות מדיניות בשם שאפשר להחיל על מכשיר בלחיצה. בכל החלה נוצרת גרסת מדיניות חדשה.",
      el("div", { class: "row end" }, el("button", { class: "primary", onclick: () => openProfileEditor(null) }, "פרופיל חדש"))),
    list,
  ];
}

async function openProfileEditor(p) {
  catalog = catalog || await api("/v1/admin/catalog");
  const selected = new Set((p?.settings?.options) || []);
  let level = p?.level ?? 2;
  const nameInput = el("input", { value: p?.name || "", placeholder: "שם הפרופיל", maxlength: 60 });
  const kw = el("input", { type: "checkbox", checked: (p?.settings?.keywords_effective?.enabled ?? true) });
  const body = el("div", { class: "stack" },
    el("label", {}, "שם", nameInput),
    el("label", {}, "רמת סינון תמונות"),
    levelPicker(level, (l) => { level = l; return true; }),
    el("label", {}, "חסימות (כמו בעמוד המכשיר)"),
    policyEditor(selected, {}),
    el("label", { class: "row gap" }, kw, "חסימת מילות חיפוש פעילה"));
  const ok = await dialog(p ? "עריכת פרופיל" : "פרופיל חדש", body, "שמירה");
  if (!ok) return;
  const settings = { options: [...selected], keywords: { enabled: kw.checked } };
  const payload = { name: nameInput.value.trim(), level, settings };
  await attempt(() => p
    ? api(`/v1/admin/profiles/${p.id}`, { method: "PATCH", body: payload })
    : api("/v1/admin/profiles", { method: "POST", body: payload }), "נשמר");
  render();
}

function maintenanceCard(d) {
  const id = d.id;
  const pause = d.pause;
  const act = async (path, body) => {
    await attempt(() => api(`/v1/admin/devices/${id}/${path}`, { method: "POST", body }));
    render();
  };
  const rows = [];
  if (pause?.active) {
    rows.push(el("div", { class: "row gap wrap" },
      chip(`מושהה עד ${dateTime(pause.until)}${pause.reason ? " · " + pause.reason : ""}`, "warn"),
      el("button", { class: "primary small", onclick: () => act("resume", {}) }, "חזרה להגנה מלאה")));
  } else {
    const minutes = el("input", { type: "number", min: 1, max: 1440, value: 60, class: "narrow" });
    const reason = el("input", { placeholder: "סיבה (למשל: תחזוקה)" });
    rows.push(el("div", { class: "row gap wrap end" }, el("label", {}, "דקות", minutes), reason,
      el("button", { class: "ghost small", onclick: () => act("pause", { minutes: Number(minutes.value) || 60, reason: reason.value }) },
        "השהה הגנה")));
  }
  const unmanage = d.unmanage_requested
    ? chip("נשלחה בקשה להסרת ניהול — ממתין לטלפון", "warn")
    : el("button", { class: "danger small", onclick: async () => {
        if (!await confirmDialog("להסיר ניהול מהמכשיר?",
          "הטלפון יחדל להיות מנהל מכשיר בדיווח הבא (אפשר יהיה להסיר/להעביר). לא מוחק נתונים.",
          "הסרת ניהול", true)) return;
        await act("unmanage", {});
      } }, "הסרת ניהול מהמכשיר");
  const transfer = el("button", { class: "ghost small", onclick: async () => {
    if (!await confirmDialog("להחליף מכשיר?",
      "ייווצר קוד חד-פעמי (24 שעות). הטלפון החדש נרשם איתו ויורש את הלקוח, הרמה והחסימות. " +
      "הטלפון הישן יושבת ברגע שההחלפה תתבצע.", "צור קוד החלפה")) return;
    const r = await attempt(() => api(`/v1/admin/devices/${id}/transfer`, { method: "POST" }));
    await dialog("קוד החלפת מכשיר", el("div", { class: "stack" },
      el("p", { class: "code" }, r.token),
      el("p", { class: "muted small" }, "מזינים אותו בטלפון החדש (מסך ההפעלה ← 'החלפת מכשיר'). תקף 24 שעות, לשימוש חד-פעמי."),
      el("button", { type: "button", class: "ghost", onclick: () => navigator.clipboard.writeText(r.token).then(() => toast("הועתק")) }, "העתקה")),
      "סגירה");
    render();
  } }, "החלפת מכשיר");
  const applyProfile = el("button", { class: "ghost small", onclick: async () => {
    const { profiles } = await api("/v1/admin/profiles");
    const sel = el("select", {}, profiles.map((p) => el("option", { value: p.id }, `${p.name} (רמה ${p.level})`)));
    if (!await dialog("החלת פרופיל על המכשיר", el("div", { class: "stack" },
      el("p", { class: "muted small" }, "הרמה והחסימות של הפרופיל יחליפו את המדיניות הנוכחית של המכשיר."), sel),
      "החל")) return;
    await attempt(() => api(`/v1/admin/devices/${id}/apply-profile`, { method: "POST", body: { profile_id: Number(sel.value) } }), "הפרופיל הוחל");
    render();
  } }, "החלת פרופיל");
  return card("תחזוקה וניהול", "פעולות מנהל מאושרות — כולן נרשמות ביומן הפעולות (audit).",
    el("div", { class: "stack" },
      el("div", {}, el("b", {}, "פרופיל מדיניות"), el("small", {}, "החלת חבילת מדיניות מוכנה על המכשיר."), applyProfile),
      el("div", {}, el("b", {}, "השהיית הגנה (מצב תחזוקה)"),
        el("small", {}, "זמנית: סינון התמונות, המגן ונעילת ה-VPN מורפים עד שהזמן נגמר — ואז הכול חוזר לבד."),
        ...rows),
      el("div", {}, el("b", {}, "החלפת מכשיר"), el("small", {}, "העברת הלקוח והמדיניות לטלפון חדש."), transfer),
      el("div", {}, el("b", {}, "הסרת ניהול"), el("small", {}, "שחרור FilterPhone מתפקיד מנהל המכשיר."), unmanage)));
}

function keywordCard(d, patch) {
  const kw = d.settings.keywords || {};
  const eff = d.settings.keywords_effective || { enabled: true, terms: [] };
  const defaults = d.settings.keyword_defaults_count || 0;
  const toggle = el("input", { type: "checkbox", checked: eff.enabled !== false, "aria-label": "חסימת מילות חיפוש",
    onchange: async (e) => { try { await patch({ keywords: { enabled: e.target.checked } }); } catch (_) { e.target.checked = !e.target.checked; } } });
  const extra = el("textarea", { class: "rtl", rows: 3, placeholder: "מילה בכל שורה — נחסמת בנוסף לרשימת הבסיס", value: (kw.extra || []).join("\n") });
  const removed = el("textarea", { class: "rtl", rows: 2, placeholder: "מילים מרשימת הבסיס שלא לחסום במכשיר הזה", value: (kw.removed || []).join("\n") });
  return card("חסימת מילות חיפוש (נגישות)",
    "אם הלקוח מקליד או מחפש מילה מהרשימה — בכל אפליקציה ובכל שפה — המגן מחזיר אותו אחורה. " +
    "ההשוואה נעשית בטלפון בלבד; מה שמוקלד לא נשמר ולא נשלח. דורש שמגן FilterPhone יהיה פעיל.",
    el("div", { class: "app-row" }, el("label", { class: "switch" }, toggle, el("span")),
      el("div", { class: "grow" }, el("b", {}, "פעיל"),
        el("small", {}, `רשימת בסיס רב-לשונית (${defaults} מילים) + מה שתוסיפו. סה"כ פעילות: ${eff.terms.length}`))),
    el("label", {}, "מילים נוספות לחסימה", extra),
    el("label", {}, "מילים מרשימת הבסיס להתיר (לא לחסום) במכשיר הזה", removed),
    el("div", { class: "row end" }, el("button", { class: "primary", onclick: async () => {
      await patch({ keywords: { extra: lines(extra.value), removed: lines(removed.value) } });
    } }, "שמירת מילים")));
}

function vpnBypassCard(d, patch) {
  const cfg = d.settings.vpn_bypass || {};
  const eff = d.settings.vpn_bypass_effective || { enabled: true, apps: [] };
  const defaults = d.settings.vpn_bypass_defaults || [];
  const rep = d.heartbeat?.vpn_bypass || {};
  const applied = new Set(rep.applied || []);
  const mismatch = new Set(rep.mismatch || []);
  const reported = rep.certs || {};
  const installed = new Map((d.apps || []).map((a) => [a.package, a.label]));
  // השגיאה כבר מוצגת (attempt) — כאן רק לא משאירים הבטחה שנדחתה בלי טיפול
  const save = (body, msg) => patch({ vpn_bypass: body }, msg).then(() => render(), () => {});
  const toggle = el("input", { type: "checkbox", checked: eff.enabled !== false, "aria-label": "אפליקציות מחוץ ל-VPN",
    onchange: async (e) => {
      try { await patch({ vpn_bypass: { enabled: e.target.checked } }); render(); } catch (_) { e.target.checked = !e.target.checked; }
    } });
  const short = (c) => `${c.slice(0, 8)}…${c.slice(-6)}`;
  const status = (a) => {
    if (mismatch.has(a.package)) return chip("חתימה לא תואמת — נשארת בסינון", "bad");
    if (applied.has(a.package)) return chip("מחוץ ל-VPN ✓", "ok");
    if (reported[a.package]) return chip("מותקנת — תוחרג בחיבור הבא", "warn");
    return installed.has(a.package) ? chip("מותקנת") : chip("לא מותקנת");
  };
  const rows = (eff.apps || []).map((a) => {
    const pinned = (a.certs || []).length > 0;
    const seen = reported[a.package];
    const isDefault = defaults.some((x) => x.package === a.package);
    return el("div", { class: "app-row" },
      el("div", { class: "grow" }, el("b", {}, a.label || installed.get(a.package) || a.package),
        el("small", { class: "ltr" }, a.package),
        el("small", {}, pinned ? `נעול לחתימה ${short(a.certs[0])}` : seen ? `חתימה בטלפון: ${short(seen)} (לא נעול)` : "לא נעול לחתימה")),
      status(a),
      !pinned && seen ? el("button", { class: "ghost small", title: "רק אפליקציה בחתימה הזו תוחרג",
        onclick: () => save({ pins: { [a.package]: [seen] } }, "ננעל לחתימה") }, "נעילה לחתימה") : null,
      pinned ? el("button", { class: "ghost small", onclick: () => save({ pins: { [a.package]: [] } }, "הנעילה בוטלה") }, "ביטול נעילה") : null,
      el("button", { class: "ghost small", onclick: () => isDefault
        ? save({ removed: [...(cfg.removed || []), a.package] }, "הוחזרה לסינון")
        : save({ extra: (cfg.extra || []).filter((p) => p !== a.package) }, "הוחזרה לסינון") }, "החזרה לסינון"));
  });
  const removedDefaults = defaults.filter((x) => (cfg.removed || []).includes(x.package));
  const restore = removedDefaults.map((x) => el("button", { class: "ghost small",
    onclick: () => save({ removed: (cfg.removed || []).filter((p) => p !== x.package) }, `${x.label} מחוץ ל-VPN`) }, `+ ${x.label}`));
  const manual = el("input", { class: "ltr", placeholder: "com.example.app", list: `bypass-apps-${d.id}` });
  const options = el("datalist", { id: `bypass-apps-${d.id}` }, (d.apps || []).map((a) => el("option", { value: a.package }, a.label)));
  return card("אפליקציות מחוץ ל-VPN",
    "אפליקציה כאן עוברת בחיבור הרגיל של הטלפון ולא דרך השרת — בלי שום סינון של FilterPhone (גם לא DNS). " +
    "מיועד לאפליקציות שמסננות בעצמן ושלא עובדות דרך שרת, כמו FilterTube (יוטיוב מסרב לכתובת של שרת). " +
    "דפדפנים וחנויות לא ניתנים להחרגה. מומלץ לנעול לחתימה — אז אפליקציה אחרת עם אותו שם לא תוחרג.",
    el("div", { class: "app-row" }, el("label", { class: "switch" }, toggle, el("span")),
      el("div", { class: "grow" }, el("b", {}, "פעיל"),
        el("small", {}, eff.enabled === false ? "כבוי — כל האפליקציות עוברות בסינון." : `${(eff.apps || []).length} אפליקציות ברשימה`))),
    rep.lockdown_conflict ? el("p", { class: "warn-text" }, "⚠ אנדרואיד 9 ומטה עם 'חסימה בלי VPN': האפליקציות המוחרגות יישארו בלי אינטרנט. בגרסה הזו צריך לבחור בין השניים.") : null,
    eff.enabled === false ? null : el("div", { class: "apps" }, rows.length ? rows : empty("אין אפליקציות ברשימה.")),
    restore.length ? el("div", { class: "row gap wrap" }, el("small", { class: "muted" }, "ברירות מחדל שהוחזרו לסינון:"), ...restore) : null,
    eff.enabled === false ? null : el("div", { class: "row gap" }, manual, options, el("button", { class: "ghost small", onclick: () => {
      const pkg = manual.value.trim();
      if (!/^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+$/.test(pkg)) { toast("שם חבילה לא תקין", true); return; }
      save({ extra: [...(cfg.extra || []), pkg] }, "נוספה — מחוץ ל-VPN");
    } }, "הוספה")));
}

function phoneAppsCard(d, patch) {
  const rules = new Map((d.app_rules || []).map((r) => [r.package, r]));
  const known = d.known_app_hosts || {};
  const unknownNet = new Set(d.network_only_unknown || []);
  const search = el("input", { type: "search", placeholder: "חיפוש אפליקציה..." });
  const manual = el("input", { class: "ltr", placeholder: "com.example.app" });
  const list = el("div", { class: "apps" });
  const apps = [...d.apps];
  for (const p of rules.keys()) if (!apps.some((a) => a.package === p)) apps.push({ package: p, label: p, system: false, blocked: false, manual: true });
  const save = async (pkg, mode, label) => {
    const before = rules.get(pkg);
    if (mode) rules.set(pkg, { ...(before || {}), package: pkg, mode }); else rules.delete(pkg);
    try {
      const fresh = await patch({ app_rules: [...rules.values()] }, mode ? `${label}: ${APP_MODES[mode][1]}` : `${label} שוחררה`);
      (d.network_only_unknown = fresh?.network_only_unknown || d.network_only_unknown);
    } catch (_) {
      if (before) rules.set(pkg, before); else rules.delete(pkg);
      draw();
    }
  };
  const draw = () => {
    const q = search.value.trim().toLowerCase();
    list.replaceChildren(...apps.filter((a) => !q || (a.label || "").toLowerCase().includes(q) || a.package.includes(q)).map((a) => {
      const rule = rules.get(a.package);
      const viaOption = a.blocked && !rule;
      const select = el("select", { "aria-label": `מצב חסימה — ${a.label || a.package}`, disabled: viaOption,
        onchange: (e) => save(a.package, e.target.value, a.label || a.package) },
        Object.entries(APP_MODES).map(([k, [, t]]) => el("option", { value: k, selected: (rule?.mode || "") === k }, t)));
      const netInfo = rule && rule.mode !== "accessibility"
        ? (known[a.package]?.length || rule.hosts?.length ? chip(`${(known[a.package] || []).length + (rule.hosts || []).length} שרתים`, "accent")
          : chip("אין שרתים ידועים — חסימה ברשת לא תשפיע", "warn")) : null;
      return el("div", { class: "app-row" },
        el("div", { class: "grow" }, el("b", {}, a.label || a.package), el("small", {}, a.package)),
        viaOption ? chip("חסום דרך אפשרות", "violet") : netInfo, a.system ? chip("מערכת") : null, select);
    }));
  };
  search.addEventListener("input", draw);
  draw();
  const add = el("div", { class: "row gap" }, manual, el("button", { class: "ghost small", onclick: () => {
    const pkg = manual.value.trim();
    if (!/^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+$/.test(pkg)) { toast("שם חבילה לא תקין", true); return; }
    if (!apps.some((a) => a.package === pkg)) apps.unshift({ package: pkg, label: pkg, system: false, blocked: false, manual: true });
    manual.value = "";
    save(pkg, "accessibility", pkg).then(draw);
  } }, "הוספה (חסימה בטלפון)"));
  return card("האפליקציות בטלפון",
    `${d.apps.length} אפליקציות${d.apps_at ? ` · עודכן ${ago(d.apps_at)}` : ""}. לכל אפליקציה: 'רשת' = השרתים שלה חסומים (נפתחת אבל לא מתחברת), 'בטלפון' = לא נפתחת (מגן FilterPhone / מנהל מכשיר), 'רשת + טלפון' = שניהם.`,
    d.apps.length ? null : el("p", { class: "muted small" }, "הטלפון עוד לא שלח את רשימת האפליקציות — אפשר להוסיף שם חבילה ידנית."),
    el("div", { class: "row gap" }, el("div", { class: "grow" }, search), add), list);
}

async function editCustomer(c) {
  const f = {
    name: el("input", { value: c.name || "" }),
    phone: el("input", { value: c.phone || "", class: "ltr" }),
    email: el("input", { value: c.email || "", class: "ltr", type: "email" }),
    payment: el("select", {}, Object.entries(PAYMENT).map(([k, [, t]]) => el("option", { value: k, selected: c.payment === k }, t))),
    paid_until: el("input", { type: "date", value: toDateInput(c.paid_until) }),
    notes: el("textarea", { rows: 3, value: c.notes || "" }),
  };
  const ok = await dialog("פרטי לקוח", el("div", { class: "stack" },
    el("label", {}, "שם", f.name), el("label", {}, "טלפון", f.phone), el("label", {}, "מייל", f.email),
    el("div", { class: "grid two" }, el("label", {}, "תשלום", f.payment), el("label", {}, "בתוקף עד", f.paid_until)),
    el("label", {}, "הערות", f.notes)), "שמירה");
  if (!ok) return false;
  await attempt(() => api(`/v1/admin/customers/${c.id}`, { method: "PATCH", body: {
    name: f.name.value, phone: f.phone.value, email: f.email.value, payment: f.payment.value,
    paid_until: fromDateInput(f.paid_until.value), notes: f.notes.value,
  } }), "פרטי הלקוח נשמרו");
  return true;
}

// ---------------------------------------------------------------- לקוחות

async function viewCustomers() {
  const list = await api("/v1/admin/customers");
  const search = el("input", { type: "search", placeholder: "חיפוש לפי שם, טלפון או מייל..." });
  const body = el("tbody");
  const now = Date.now() / 1000;
  const draw = () => {
    const q = search.value.trim().toLowerCase();
    const rows = list.filter((c) => !q || [c.name, c.phone, c.email].some((x) => (x || "").toLowerCase().includes(q)));
    body.replaceChildren(...rows.map((c) => el("tr", {},
      el("td", {}, el("b", {}, c.name), c.notes ? el("small", {}, c.notes.slice(0, 60)) : null),
      el("td", {}, telLink(c.phone), el("small", {}, mailLink(c.email))),
      el("td", {}, el("a", { href: `#/devices?customer=${c.id}` }, `${c.active}/${c.devices} פעילים`)),
      el("td", {}, paymentChip(c.payment)),
      el("td", { class: "nowrap" }, c.paid_until ? el("span", { class: c.paid_until < now + 7 * 86400 ? "warn-text" : "" }, date(c.paid_until)) : "—"),
      el("td", {}, el("div", { class: "actions" }, el("button", { class: "ghost small", onclick: async () => {
        if (await editCustomer(c)) render();
      } }, "עריכה"))))));
    if (!rows.length) body.replaceChildren(el("tr", {}, el("td", { colspan: 6 }, empty("אין לקוחות עדיין. לקוחות נוצרים מבקשות הצטרפות באפליקציה."))));
  };
  search.addEventListener("input", draw);
  draw();
  return [search, el("section", { class: "card" }, el("div", { class: "table-wrap" }, el("table", {},
    el("thead", {}, el("tr", {}, el("th", {}, "לקוח"), el("th", {}, "קשר"), el("th", {}, "מכשירים"), el("th", {}, "תשלום"),
      el("th", {}, "בתוקף עד"), el("th", {}))), body)))];
}

// ---------------------------------------------------------------- אירועים

async function viewEvents(_, query) {
  const devices = await api("/v1/admin/devices");
  const f = {
    device: el("select", {}, el("option", { value: "" }, "כל המכשירים"),
      devices.filter((d) => d.vpn_ip).map((d) => el("option", { value: d.id, selected: query.get("device") === String(d.id) }, d.name))),
    kind: el("select", {}, el("option", { value: "" }, "כל הסוגים"),
      Object.entries(KINDS).map(([k, t]) => el("option", { value: k, selected: query.get("kind") === k }, t))),
    action: el("select", {}, el("option", { value: "" }, "כל הפעולות"),
      Object.entries(ACTIONS).map(([k, t]) => el("option", { value: k, selected: query.get("action") === k }, t))),
    q: el("input", { type: "search", placeholder: "חיפוש אתר...", value: query.get("q") || "" }),
  };
  const box = el("section", { class: "card" }, loading());
  const load = async () => {
    const p = new URLSearchParams({ limit: "300" });
    for (const k of ["device", "kind", "action", "q"]) if (f[k].value) p.set(k, f[k].value);
    box.replaceChildren(eventsTable(await api(`/v1/admin/events?${p}`)));
  };
  Object.values(f).forEach((x) => x.addEventListener("change", load));
  f.q.addEventListener("input", () => { clearTimeout(f.q.t); f.q.t = setTimeout(load, 350); });
  load();
  autoRefresh(load, 15000);
  topAction("רענון", load);
  return [el("div", { class: "row gap" }, f.device, f.kind, f.action, el("div", { class: "grow" }, f.q)), box];
}

// ---------------------------------------------------------------- בדיקת תמונה

async function viewTest() {
  const url = el("input", { type: "url", class: "ltr", placeholder: "https://example.com/image.jpg" });
  const file = el("input", { type: "file", accept: "image/*" });
  const level = el("select", {}, catalog.levels.filter((l) => l.level > 0).map((l) =>
    el("option", { value: l.level, selected: l.level === 2 }, `${l.level} · ${l.name}`)));
  const out = el("div", { class: "stack" });
  const run = async (e) => {
    e?.preventDefault();
    const body = { level: Number(level.value) };
    if (file.files[0]) {
      body.data = await new Promise((resolve, reject) => {
        const r = new FileReader();
        r.onload = () => resolve(r.result);
        r.onerror = reject;
        r.readAsDataURL(file.files[0]);
      });
    } else if (url.value.trim()) body.url = url.value.trim();
    else { toast("הדביקו כתובת או בחרו קובץ", true); return; }
    out.replaceChildren(loading());
    try {
      out.replaceChildren(...testResult(await api("/v1/admin/test-image", { method: "POST", body }), Number(level.value)));
    } catch (err) {
      out.replaceChildren(card("שגיאה", null, el("p", { class: "error" }, err.message)));
    }
  };
  return [card("בדיקת תמונה", "בודק תמונה בדיוק כמו שהיא עוברת בטלפון: ציונים, החלטה בכל רמה, ומה הטלפון מקבל. התמונה המקורית לא מוצגת.",
    el("form", { class: "stack", onsubmit: run },
      el("label", {}, "כתובת תמונה", url),
      el("div", { class: "grid three" }, el("label", {}, "או קובץ מהמחשב", file), el("label", {}, "רמה להצגת התוצאה", level),
        el("div", { class: "row end" }, el("button", { class: "primary", type: "submit" }, "בדיקה"))))), out];
}

function testResult(r, lv) {
  const i = r.info;
  const decisions = el("div", { class: "grid three" }, [1, 2, 3].map((l) => {
    const d = i.decisions[l] || i.decisions[String(l)];
    const painted = d?.mode === "body" ? "נצבעת (כל הגוף)" : "נצבעת (עור חשוף)";
    const word = { block: "נחסמת", paint: painted, replace: painted, pass: "עוברת" }[d?.action] || d?.action;
    return el("div", { class: `decision ${d?.action === "paint" ? "replace" : d?.action}` },
      el("b", {}, `רמה ${l} · ${levelName(l)}: ${word}`), el("span", { class: "small ltr mono" }, d?.reason || ""));
  }));
  const scores = i.scores ? el("div", { class: "stack" }, [["person", "אדם"], ["female", "אישה"], ["immodest", "לבוש לא צנוע"], ["nsfw", "עירום"]]
    .map(([k, t]) => el("div", { class: "row gap" }, el("span", { class: "grow" }, t), meter(i.scores[k], i.scores[k] > 0.5),
      el("span", { class: "mono nowrap" }, i.scores[k].toFixed(3))))) : el("p", { class: "muted" }, `לא נבדקה: ${i.note || "לא ניתן לפענח"}`);
  const seg = i.segmentation;
  return [
    card("ההחלטה", `${i.image ? `${i.image.width}×${i.image.height} ${i.image.format || ""}` : ""} · ${i.ms} מ"ש · שלבים: ${(i.steps || []).join(", ") || "סיווג בלבד"}`, decisions),
    el("div", { class: "grid two" },
      card("ציונים", "0 = בטוח שלא, 1 = בטוח שכן", scores,
        seg ? el("p", { class: "muted small" }, `סגמנטציה: נשים ${(seg.person * 100).toFixed(1)}% מהתמונה · עור גלוי אצלן ${(seg.skin * 100).toFixed(1)}% · גברים (לא נצבעים) ${((seg.others || 0) * 100).toFixed(1)}% · ${seg.crops} חיתוכים`) : null,
        seg?.people?.length ? el("div", { class: "chips" }, seg.people.map(([, , , , score, woman]) =>
          chip(`${woman ? "אישה" : "גבר"} ${score >= 0 ? score.toFixed(2) : "(לפי שאר התמונה)"}`, woman ? "warn" : "ok"))) : null,
        i.detections?.length ? el("div", { class: "chips" }, i.detections.map(([label, score]) => chip(`${label} ${score}`))) : null),
      card(`מה הטלפון מקבל ברמה ${lv}`, null, r.result_image
        ? el("img", { src: r.result_image, class: "result-img", alt: "התוצאה אחרי הסינון" })
        : el("p", { class: "decision pass" }, "התמונה עוברת כמו שהיא ברמה הזו."))),
  ];
}

// ---------------------------------------------------------------- קודים

async function viewCodes() {
  const list = await api("/v1/admin/codes");
  const def = catalog.defaults || { level: 1, options: [] };
  const codeOpts = new Set(def.options);                 // ברירת המחדל ללקוח חדש — ניתן לערוך
  const levelSel = el("select", { name: "level" }, catalog.levels.filter((l) => l.level > 0).map((l) =>
    el("option", { value: l.level, selected: l.level === def.level }, `${l.level} · ${l.name}`)));
  const form = el("form", { class: "stack" },
    el("div", { class: "grid three" },
      el("label", {}, "שם הלקוח", el("input", { name: "label", placeholder: "למשל: משפחת כהן" })),
      el("label", {}, "רמה", levelSel),
      el("label", {}, "מספר מכשירים", el("input", { name: "uses", type: "number", min: 1, max: 100, value: 1 })),
      el("label", {}, "תוקף (ימים)", el("input", { name: "expires_days", type: "number", min: 1, max: 365, value: 30 }))),
    el("details", { class: "policy-box" },
      el("summary", {}, `חסימות ברירת המחדל (${def.options.length}) — אפשר לשנות לפני היצירה`),
      policyEditor(codeOpts)),
    el("div", { class: "row end" }, el("button", { class: "primary", type: "submit" }, "צור קוד")));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(form);
    const r = await attempt(() => api("/v1/admin/codes", { method: "POST", body: {
      label: f.get("label"), level: Number(f.get("level")), uses: Number(f.get("uses")),
      expires_days: Number(f.get("expires_days")), options: [...codeOpts],
    } }));
    await dialog("קוד ההפעלה", el("div", { class: "stack" }, el("p", { class: "code" }, r.code),
      el("p", { class: "muted small" }, "מוצג פעם אחת בלבד. שולחים ללקוח יחד עם קישור ההורדה."),
      el("button", { type: "button", class: "ghost", onclick: () => navigator.clipboard.writeText(r.code).then(() => toast("הועתק")) }, "העתקה")), "סגירה");
    render();
  });
  const qrForm = el("form", { class: "grid three" },
    el("label", {}, "שם הלקוח", el("input", { name: "label", placeholder: "למשל: משפחת כהן" })),
    el("label", {}, "רמה", el("select", { name: "level" }, catalog.levels.filter((l) => l.level > 0).map((l) => el("option", { value: l.level }, `${l.level} · ${l.name}`)))),
    el("label", {}, "Wi-Fi (לא חובה)", el("input", { name: "wifi_ssid", placeholder: "שם הרשת" })),
    el("label", {}, "סיסמת Wi-Fi", el("input", { name: "wifi_password", type: "password", autocomplete: "off" })),
    el("div", { class: "row end" }, el("button", { class: "primary", type: "submit" }, "צור QR")));
  qrForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(qrForm);
    const r = await attempt(() => api("/v1/admin/provisioning", { method: "POST", body: {
      label: f.get("label"), level: Number(f.get("level")), wifi_ssid: f.get("wifi_ssid"), wifi_password: f.get("wifi_password"),
    } }));
    await dialog("הקמת מכשיר מנוהל (Device Owner)", el("div", { class: "stack" },
      el("img", { src: r.qr, alt: "QR להקמה", class: "qr" }),
      el("ol", { class: "small" },
        el("li", {}, "טלפון מאופס להגדרות יצרן (חדש, או אחרי איפוס). אין להוסיף חשבון Google לפני ההקמה."),
        el("li", {}, "במסך הפתיחה הראשון: הקשה 6 פעמים באותו מקום → נפתח סורק QR (באנדרואיד 8 צריך להתחבר קודם ל-Wi-Fi)."),
        el("li", {}, "סורקים את הקוד. הטלפון מוריד את FilterPhone, מאמת את הטביעה, ומגדיר אותה כמנהלת המכשיר."),
        el("li", {}, "בסיום FilterPhone נפתחת ומתחברת לבד (הקוד החד-פעמי שבתוך ה-QR). מאשרים חיבור VPN ומתקינים תעודה אם מתבקש.")),
      el("p", { class: "muted small" }, `קוד חד-פעמי, תקף יומיים: ${r.code.code}. טביעת ה-APK: ${r.apk_sha256.slice(0, 16)}…`),
      el("p", { class: "muted small" }, "ה-QR מכיל את הקוד ואת סיסמת ה-Wi-Fi — לא לשלוח בקבוצות.")), "סגירה");
    render();
  });
  return [
    card("קוד הפעלה חדש", "הדרך המהירה בלי טופס הרשמה: הלקוח מזין קוד באפליקציה ומתחבר מיד ברמה שבחרת.", form),
    card("הקמת מכשיר מנוהל ב-QR (Device Owner)", "לטלפון מאופס בלבד. FilterPhone הופכת למנהלת המכשיר: אי אפשר להסיר אותה, ה-VPN קבוע, והגדרות המנהל נאכפות ע\"י אנדרואיד.", qrForm),
    card("קודים קיימים", null, list.length ? el("div", { class: "table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {}, el("th", {}, "לקוח"), el("th", {}, "מסתיים ב-"), el("th", {}, "רמה"), el("th", {}, "שימושים שנותרו"), el("th", {}, "תוקף"), el("th", {}))),
      el("tbody", {}, list.map((c) => el("tr", {},
        el("td", {}, c.label || "—"), el("td", { class: "ltr mono" }, `…${c.hint}`), el("td", {}, levelBadge(c.level)),
        el("td", {}, c.uses_left), el("td", {}, date(c.expires_at)),
        el("td", {}, el("div", { class: "actions" }, el("button", { class: "danger small", onclick: async () => {
          if (!await confirmDialog("לבטל את הקוד?", "אי אפשר יהיה להפעיל איתו מכשירים חדשים.", "ביטול הקוד", true)) return;
          await attempt(() => api(`/v1/admin/codes/${c.id}`, { method: "DELETE" }), "הקוד בוטל");
          render();
        } }, "ביטול")))))))) : empty("אין קודים.")),
  ];
}

// ---------------------------------------------------------------- יומן

async function viewAudit() {
  const list = await api("/v1/admin/audit?limit=300");
  const ACT = { register: "הרשמה", approve: "אישור", reject: "דחייה", device: "עדכון מכשיר", delete: "מחיקה", enroll: "הפעלה בקוד",
    customer: "עדכון לקוח", code: "קוד חדש", code_delete: "ביטול קוד", reset_apps: "איפוס אפליקציות", cancel: "ביטול הרשמה", request_change: "בקשת שינוי" };
  const link = (t) => {
    const m = /^(device|request):(\d+)$/.exec(t || "");
    if (!m) return t;
    return el("a", { href: m[1] === "device" ? `#/devices/${m[2]}` : "#/requests?status=all" }, t);
  };
  return card(null, null, list.length ? el("div", { class: "table-wrap" }, el("table", {},
    el("thead", {}, el("tr", {}, el("th", {}, "זמן"), el("th", {}, "מי"), el("th", {}, "פעולה"), el("th", {}, "על מה"), el("th", {}, "פרטים"))),
    el("tbody", {}, list.map((a) => el("tr", {},
      el("td", { class: "nowrap" }, dateTime(a.ts)), el("td", {}, a.actor), el("td", {}, ACT[a.action] || a.action),
      el("td", {}, link(a.target)), el("td", { class: "small" }, a.detail)))))) : empty("היומן ריק."));
}

// ---------------------------------------------------------------- מערכת

function whatsappCard(wa) {
  if (!wa) return null;
  const f = (key, label, attrs = {}) => {
    const input = el("input", { value: wa[key] || "", ...attrs });
    return [input, el("label", {}, label, input)];
  };
  const [adminNumber, adminL] = f("admin_number", "הוואטסאפ שלך (לשם יגיעו הפניות)", { class: "ltr", placeholder: "0501234567", inputmode: "tel" });
  const [phoneId, phoneIdL] = f("phone_number_id", "Phone number ID (מ-WhatsApp Manager)", { class: "ltr", inputmode: "numeric" });
  const tokenIn = el("input", { type: "password", class: "ltr", autocomplete: "off",
    placeholder: wa.token_set ? `שמור (${wa.token_hint || "…"}) — השאירו ריק בלי שינוי` : "Access token (System user, קבוע)" });
  const secretIn = el("input", { type: "password", class: "ltr", autocomplete: "off",
    placeholder: wa.app_secret_set ? "שמור — השאירו ריק בלי שינוי" : "App Secret (App settings → Basic)" });
  const [verify, verifyL] = f("verify_token", "Verify token (מחרוזת שתדביקו גם ב-Meta)", { class: "ltr" });
  const gen = el("button", { class: "ghost small", type: "button", onclick: () => {
    const b = new Uint8Array(18); crypto.getRandomValues(b);
    verify.value = Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
  } }, "יצירה");
  const [pub, pubL] = f("public_number", "מספר לפנייה ישירה בוואטסאפ (רשות — מוצג ללקוחות)", { class: "ltr", inputmode: "tel" });
  const [tpl, tplL] = f("template", "תבנית להתראה אחרי 24 שעות (רשות)", { class: "ltr", placeholder: "support_message" });
  const save = async () => {
    await attempt(() => api("/v1/admin/whatsapp", { method: "PUT", body: {
      admin_number: adminNumber.value, phone_number_id: phoneId.value, token: tokenIn.value, app_secret: secretIn.value,
      verify_token: verify.value, public_number: pub.value, template: tpl.value } }), "נשמר");
    render();
  };
  const copy = (v) => navigator.clipboard?.writeText(v).then(() => toast("הועתק"));
  return card("תמיכה בוואטסאפ", "פניות מהצ'אט באפליקציה נשלחות לוואטסאפ שלך. עונים ב\"השב\" על ההודעה — והתשובה מגיעה ללקוח באפליקציה (גם ללקוח בלי וואטסאפ).",
    el("div", { class: "chips" },
      chip(wa.can_send ? "שליחה אליך: פעיל" : "שליחה אליך: לא מוגדר", wa.can_send ? "ok" : "warn"),
      chip(wa.can_receive ? "תשובות מוואטסאפ: פעיל" : "תשובות מוואטסאפ: לא מוגדר", wa.can_receive ? "ok" : "warn"),
      wa.last_ok ? chip(`נשלח בהצלחה ${ago(wa.last_ok)}`) : null),
    wa.last_error ? el("p", { class: "error small" }, `שגיאה אחרונה: ${wa.last_error}`) : null,
    el("div", { class: "grid two" }, adminL, phoneIdL,
      el("label", {}, "Access token", tokenIn), el("label", {}, "App Secret", secretIn),
      el("div", { class: "row gap end" }, el("div", { class: "grow" }, verifyL), gen), pubL, tplL),
    el("div", { class: "row gap wrap" }, el("span", { class: "muted small" }, "כתובת ה-Webhook:"),
      el("code", { class: "ltr" }, wa.webhook_url), el("button", { class: "ghost small", onclick: () => copy(wa.webhook_url) }, "העתקה")),
    el("p", { class: "muted small" }, "בוט שמחובר לוואטסאפ שלך ב-QR ועונה ללקוחות עם AI — בעמוד ",
      el("a", { href: "#/wabot" }, "בוט וואטסאפ"), "."),
    el("details", {}, el("summary", {}, "איך מחברים (פעם אחת, כרבע שעה)"),
      el("ol", { class: "small steps" },
        el("li", {}, "ב-developers.facebook.com: My Apps → Create app → Business, ומוסיפים את המוצר WhatsApp."),
        el("li", {}, "WhatsApp → API Setup: מעתיקים את Phone number ID. מספר עסקי — מספר שלא רשום באפליקציית וואטסאפ רגילה (או מספר הבדיקה של Meta)."),
        el("li", {}, "Business settings → System users: יוצרים משתמש (Admin) → Generate token עם whatsapp_business_messaging ו-whatsapp_business_management. מדביקים כאן ב-Access token."),
        el("li", {}, "App settings → Basic → App Secret: מדביקים כאן."),
        el("li", {}, "לוחצים \"יצירה\" ליד Verify token ושומרים. ב-WhatsApp → Configuration → Webhook: Callback URL = הכתובת למעלה, Verify token = אותה מחרוזת → Verify and save → Subscribe ל-messages."),
        el("li", {}, "שולחים הודעה כלשהי מהוואטסאפ שלך למספר העסקי (פותח חלון של 24 שעות). להתראות גם אחרי 24 שעות: תבנית Utility בשם support_message, בעברית, עם הגוף: פנייה חדשה מ-{{1}}: {{2}}"),
        el("li", {}, "\"הודעת בדיקה\" — ואמורה להגיע אליך הודעה."))),
    el("div", { class: "row end gap" },
      wa.token_set || wa.app_secret_set ? el("button", { class: "danger small", onclick: async () => {
        if (!await confirmDialog("למחוק את הטוקן וה-App Secret?", "השליחה לוואטסאפ תיפסק עד שתזינו אותם שוב.", "מחיקה", true)) return;
        await attempt(() => api("/v1/admin/whatsapp", { method: "PUT", body: { clear_secrets: true } }), "נמחק");
        render();
      } }, "מחיקת הסודות") : null,
      el("button", { class: "ghost", disabled: !wa.can_send, onclick: () => attempt(() => api("/v1/admin/whatsapp/test", { method: "POST" }), "נשלחה הודעת בדיקה").then(render, render) }, "הודעת בדיקה"),
      el("button", { class: "primary", onclick: save }, "שמירה")));
}

function brandingCard(b) {
  if (!b) return null;
  const name = el("input", { value: b.org_name || "", maxlength: 60 });
  return card("שם הארגון בטלפונים", "בטלפון שמנוהל במלואו (מנהל מכשיר), אנדרואיד כותב במסך הנעילה ובפס ההתראות: \"המכשיר הזה שייך ל-…\". כאן קובעים מה יופיע במקום \"הארגון שלך\". הטלפונים מתעדכנים בסנכרון הבא.",
    el("div", { class: "row gap end" }, el("label", { class: "grow" }, "שם הארגון", name),
      el("button", { class: "primary", onclick: async () => {
        await attempt(() => api("/v1/admin/branding", { method: "PUT", body: { org_name: name.value } }), "נשמר");
        render();
      } }, "שמירה")));
}

function gpuCard(g) {
  if (!g) return null;
  const fromEnv = g.source === "env";
  const url = el("input", { class: "ltr", value: g.url || "", placeholder: "https://השם-שלך--filterphone-engine.modal.run", disabled: fromEnv });
  const key = el("input", { class: "ltr", value: g.key || "", placeholder: "wk-...", autocomplete: "off", disabled: fromEnv });
  const secret = el("input", { type: "password", class: "ltr", autocomplete: "off", disabled: fromEnv,
    placeholder: g.secret_set ? "שמור — השאירו ריק בלי שינוי" : "ws-..." });
  const MODES = [["always", "תמיד — כל תמונה חדשה נבדקת ב-GPU"], ["overflow", "רק כשהשרת עמוס (חוסך קרדיט)"], ["off", "כבוי — הכול בשרת הרגיל"]];
  const mode = el("select", { disabled: fromEnv }, MODES.map(([v, t]) => el("option", { value: v, selected: g.mode === v }, t)));
  const result = el("p", { class: "small" });
  const save = async () => {
    await attempt(() => api("/v1/admin/gpu", { method: "PUT", body: { url: url.value, key: key.value, secret: secret.value, mode: mode.value } }), "נשמר — המנוע יקלוט תוך כ-15 שניות");
    render();
  };
  const test = async (btn) => {
    btn.disabled = true;
    result.className = "small muted";
    result.textContent = "בודק… (בפעם הראשונה עד דקה — Modal מעלה את המודלים ל-GPU)";
    try {
      const r = await api("/v1/admin/gpu/test", { method: "POST" });
      result.className = r.ok ? "small ok-text" : "error small";
      result.textContent = r.ok ? `✓ מחובר — תשובה תוך ${r.ms}ms` : `✗ ${r.error}`;
    } catch (e) {
      result.className = "error small";
      result.textContent = `✗ ${e.message}`;
    } finally {
      btn.disabled = false;
    }
  };
  return card("שרת GPU לסינון תמונות (Modal)", "ניתוח התמונות רץ על כרטיס גרפי ב-Modal: אותם מודלים ואותה החלטה בדיוק — רק מהר יותר. כל תקלה, איטיות או קרדיט שנגמר — חוזרים מיד לשרת הרגיל, בלי שהלקוח מרגיש. תמונה שכבר נבדקה לא נשלחת שוב (מטמון).",
    el("div", { class: "chips" },
      chip(g.active ? "פעיל" : g.configured ? "כבוי" : "לא מוגדר", g.active ? "ok" : "warn"),
      fromEnv ? chip("מוגדר בקובץ השרת (FP_MODAL_*)") : null,
      chip(`גרסת מודלים: ${g.heads}`)),
    el("div", { class: "grid two" },
      el("label", {}, "כתובת (URL) מ-Modal", url), el("label", {}, "מצב", mode),
      el("label", {}, "Token ID", key), el("label", {}, "Token Secret", secret)),
    result,
    el("details", {}, el("summary", {}, "איך מחברים (פעם אחת)"),
      el("p", { class: "muted small" }, "המדריך המלא בעברית: docs/MODAL.md בפרויקט. בקצרה:"),
      el("ol", { class: "small steps" },
        el("li", {}, "נרשמים ב-modal.com."),
        el("li", {}, "מעלים את שרת הסינון: ב-GitHub → Actions → \"העלאה ל-Modal\" → Run workflow (צריך את הסודות MODAL_TOKEN_ID ו-MODAL_TOKEN_SECRET)."),
        el("li", {}, "בסוף ההעלאה מופיעה כתובת שמסתיימת ב-filterphone-engine.modal.run — מדביקים כאן."),
        el("li", {}, "ב-modal.com → Settings → Proxy Auth Tokens → New: מדביקים כאן את Token ID ו-Token Secret."),
        el("li", {}, "שמירה ← \"בדיקת חיבור\"."))),
    el("div", { class: "row end gap" },
      g.source === "dashboard" ? el("button", { class: "danger small", onclick: async () => {
        if (!await confirmDialog("להסיר את חיבור ה-GPU?", "הסינון ימשיך כרגיל בשרת עצמו.", "הסרה", true)) return;
        await attempt(() => api("/v1/admin/gpu", { method: "PUT", body: { clear: true } }), "הוסר");
        render();
      } }, "הסרה") : null,
      el("button", { class: "ghost", disabled: !g.configured, onclick: (ev) => test(ev.currentTarget) }, "בדיקת חיבור"),
      fromEnv ? null : el("button", { class: "primary", onclick: save }, "שמירה")));
}

// ---------------------------------------------------------------- בוט וואטסאפ

const WABOT_STATE = {
  not_running: ["השירות לא רץ בשרת", "bad"], off: ["כבוי", "warn"], starting: ["מתחבר…", "warn"],
  qr: ["ממתין לסריקת QR", "warn"], ready: ["מחובר", "ok"], disconnected: ["נותק — מתחבר מחדש", "bad"], error: ["שגיאה", "bad"],
};
const AI_KEY_LINKS = { gemini: ["https://aistudio.google.com/apikey", "קבלת מפתח Gemini (חינם)"],
  groq: ["https://console.groq.com/keys", "קבלת מפתח Groq (חינם)"] };

let wabotLogOpen = false;     // היומן נשאר פתוח בין רענונים

function wabotStatus(v) {
  const st = v.status || {};
  const [text, kind] = WABOT_STATE[st.state] || [st.state || "—", ""];
  const label = st.label || v.label_name || "בוט";
  const lists = { on: [`רשימת "${label}" פעילה`, "ok"], missing: [`אין רשימה בשם "${label}"`, "warn"],
    not_business: ["וואטסאפ רגיל — בלי רשימות", "warn"] }[st.labels];
  const stats = st.stats || {};
  const parts = [el("div", { class: "chips" }, chip(text, kind),
    st.me ? chip(`+${st.me}`) : null, st.ai ? chip(`AI: ${st.ai}`) : null,
    lists ? chip(lists[0], lists[1]) : null, st.version ? chip(`גרסה ${st.version}`) : null,
    v.learn_from_replies !== false ? chip("מצב לימוד", "violet") : null)];
  if (st.error) parts.push(el("p", { class: "error small" }, st.error));
  if (st.state === "not_running") {
    parts.push(el("p", { class: "muted small" }, "הבוט מותקן בשרת אוטומטית בהעלאה (deploy). אם זה לא מסתדר תוך כמה דקות — בשרת: journalctl -u fp-wabot -n 50"));
  } else if (st.state === "off") {
    parts.push(el("p", { class: "muted small" }, "מדליקים את \"הבוט פעיל\" למטה ושומרים — ותוך כמה שניות מופיע כאן QR לסריקה."));
  } else if (st.state === "starting") {
    if (st.progress != null) {
      parts.push(el("p", { class: "small" }, `טוען את הצ'אטים מוואטסאפ: ${st.progress}%`),
        el("progress", { max: 100, value: st.progress, style: "width:100%" }));
    } else {
      parts.push(el("p", { class: "muted small" }, "פותח את וואטסאפ ווב בשרת (עד כמה דקות בפעם הראשונה)…"));
    }
    parts.push(el("p", { class: "muted small" }, "אם בוואטסאפ כבר רואים \"מחובר\" והטעינה לא מסתיימת תוך 2 דקות — הבוט מאתחל את עצמו לבד (החיבור שמור, בלי לסרוק שוב)."));
  } else if (st.state === "qr" && v.qr_image) {
    parts.push(el("div", { class: "stack" },
      el("img", { class: "qr", alt: "QR לחיבור הוואטסאפ", src: v.qr_image }),
      el("ol", { class: "small steps" },
        el("li", {}, "בטלפון: פותחים את וואטסאפ (ביזנס) → ⋮ למעלה → מכשירים מקושרים → קישור מכשיר."),
        el("li", {}, "סורקים את הקוד. הוא מתחלף כל כמה שניות — העמוד מתעדכן לבד."),
        el("li", {}, "אחרי הסריקה החיבור נשמר בשרת — לא צריך לסרוק שוב."))));
  } else if (st.state === "ready") {
    parts.push(kv([
      ["תשובות של הבוט", num(stats.replies)], ["הועברו אליך", num(stats.handoffs)],
      ["למד מהתשובות שלך", num(stats.learned)], ["שאל אותך (לא ידע לענות)", num(stats.asked)],
      ["פניות מהאפליקציה שהגיעו אליך", num(stats.app_forwarded)], ["ענית ללקוחות באפליקציה", num(stats.app_replies)],
      stats.ai_errors ? ["שגיאות AI", num(stats.ai_errors)] : null,
      ["הודעות הבוט אליך", v.notify_to ? `למספר ${v.notify_to}` : "לצ'אט עם עצמך"],
    ]));
    if (st.labels === "missing") {
      parts.push(el("p", { class: "small" }, `כדי לנהל מתוך הוואטסאפ: בוואטסאפ ביזנס → צ'אטים → רשימות → ＋ → רשימה בשם "${label}". עד אז הבוט עונה לכולם ושותק בשיחה שבה כתבת.`));
    }
  }
  if ((st.log || []).length) {
    const d = el("details", { open: wabotLogOpen },
      el("summary", {}, "יומן הבוט (מה קורה עכשיו)"),
      el("pre", { class: "small", dir: "rtl", style: "white-space:pre-wrap;max-height:260px;overflow:auto;margin:6px 0 0" },
        st.log.join("\n")));
    d.addEventListener("toggle", () => { wabotLogOpen = d.open; });
    parts.push(d);
  }
  return parts;
}

const KNOW_SOURCE = { reply: ["מהתשובה שלך ללקוח", ""], ask: ["שאל אותך וענית", "ok"],
  teach: ["לימדת בוואטסאפ", "violet"], dashboard: ["נוסף בדשבורד", "violet"] };

/** הוספה / עריכה של פריט ידע (שאלה + תשובה, או עובדה). מחזיר true אם נשמר. */
async function editKnowledge(it) {
  const kind = el("select", { disabled: Boolean(it) }, [["qa", "שאלה ותשובה"], ["fact", "עובדה"]]
    .map(([val, t]) => el("option", { value: val, selected: (it?.kind || "qa") === val }, t)));
  const question = el("textarea", { rows: 2, value: it?.question || "", placeholder: "למשל: כמה עולה סינון למכשיר?" });
  const answer = el("textarea", { rows: 4, value: it?.answer || "",
    placeholder: "התשובה, כמו שהיית עונה ללקוח (בלי פרטים אישיים)" });
  const qLabel = el("label", {}, "השאלה של הלקוח", question);
  const aTitle = el("span");
  const sync = () => {
    qLabel.hidden = kind.value === "fact";
    aTitle.textContent = kind.value === "fact" ? "העובדה" : "התשובה";
  };
  kind.addEventListener("change", sync);
  sync();
  if (!await dialog(it ? `עריכה #${it.id}` : "ללמד את הבוט", el("div", { class: "stack" },
    it ? null : el("label", {}, "סוג", kind), qLabel, el("label", {}, aTitle, answer)),
  "שמירה")) return false;
  const body = { question: kind.value === "fact" ? "" : question.value, answer: answer.value };
  try {
    await attempt(() => (it
      ? api(`/v1/admin/wabot/knowledge/${it.id}`, { method: "PUT", body })
      : api("/v1/admin/wabot/knowledge", { method: "POST", body: { ...body, kind: kind.value } })),
    "נשמר — הבוט ישתמש בזה תוך כמה דקות");
    return true;
  } catch (_) {
    return false;
  }
}

function knowledgeRows(items, reload) {
  if (!items.length) {
    return [empty("עוד לא למד כלום. הוא לומד כשאתה עונה ללקוח בעצמך, וכשהוא שואל אותך בצ'אט עם עצמך ואתה עונה. אפשר גם ללמד אותו כאן (\"הוספה\") או בוואטסאפ.")];
  }
  const cell = (text) => el("td", { dir: "auto", style: "white-space:pre-wrap;vertical-align:top;min-width:160px" }, text);
  return [el("div", { class: "table-wrap" }, el("table", {},
    el("thead", {}, el("tr", {}, el("th", {}, "#"), el("th", {}, "שאלה / עובדה"), el("th", {}, "התשובה"),
      el("th", {}, "מאיפה"), el("th", {}, "שימושים"), el("th", {}, ""))),
    el("tbody", {}, items.map((it) => {
      const [src, kind] = KNOW_SOURCE[it.source] || [it.source, ""];
      return el("tr", {},
        el("td", { style: "vertical-align:top" }, el("span", { class: "ltr" }, String(it.id))),
        it.kind === "fact" ? cell(el("span", {}, "📌 ", el("span", { class: "muted" }, "עובדה"))) : cell(it.question),
        cell(it.answer),
        el("td", { style: "vertical-align:top;white-space:nowrap" }, chip(src, kind), el("br"),
          el("small", { class: "muted" }, dateTime(it.updated_at)),
          it.phone ? el("small", { class: "ltr muted" }, ` · +${it.phone}`) : null),
        el("td", { style: "vertical-align:top" }, num(it.uses)),
        el("td", { style: "vertical-align:top;white-space:nowrap" },
          el("button", { class: "ghost small", onclick: async () => { if (await editKnowledge(it)) reload(); } }, "עריכה"),
          el("button", { class: "danger small", onclick: async () => {
            if (!await confirmDialog(`לשכוח את #${it.id}?`, "הבוט לא ישתמש בזה יותר.", "מחיקה", true)) return;
            await attempt(() => api(`/v1/admin/wabot/knowledge/${it.id}`, { method: "DELETE" }), "נמחק").catch(() => {});
            reload();
          } }, "מחיקה")));
    }))))];
}

async function viewWabot() {
  const v = await api("/v1/admin/wabot");
  const statusBox = el("div", { class: "stack" }, wabotStatus(v));
  const knowBox = el("div", { class: "stack" }, loading());
  const knowCount = el("span", { class: "muted small" });
  const loadKnowledge = async () => {
    try {
      const { items } = await api("/v1/admin/wabot/knowledge");
      knowCount.textContent = items.length ? `${num(items.length)} פריטים` : "";
      knowBox.replaceChildren(...knowledgeRows(items, loadKnowledge));
    } catch (e) {
      knowBox.replaceChildren(el("p", { class: "error small" }, e.message));
    }
  };
  loadKnowledge();
  let lastState = v.status?.state;
  const refresh = async () => {
    try {
      const n = await api("/v1/admin/wabot");
      statusBox.replaceChildren(...wabotStatus(n));
      if (n.status?.state !== lastState) {
        lastState = n.status?.state;
        autoRefresh(refresh, ["qr", "starting", "disconnected"].includes(lastState) ? 4000 : 15000);
      }
    } catch (_) { /* הבא */ }
  };
  autoRefresh(refresh, ["qr", "starting", "disconnected"].includes(lastState) ? 4000 : 15000);

  const enabled = el("input", { type: "checkbox", checked: Boolean(v.enabled), "aria-label": "הבוט פעיל" });
  const provider = el("select", {}, [["gemini", "Gemini של גוגל (מומלץ)"], ["groq", "Groq"], ["none", "בלי AI — תשובה קבועה ומעביר אליך"]]
    .map(([val, t]) => el("option", { value: val, selected: v.ai_provider === val }, t)));
  const key = el("input", { type: "password", class: "ltr", autocomplete: "off",
    placeholder: v.key_set ? `שמור (${v.key_hint || "…"}) — השאירו ריק בלי שינוי` : "מדביקים כאן את המפתח" });
  const keyLink = el("a", { target: "_blank", rel: "noopener" });
  const syncLink = () => {
    const l = AI_KEY_LINKS[provider.value];
    keyLink.hidden = !l;
    if (l) { keyLink.href = l[0]; keyLink.textContent = `${l[1]} ↗`; }
  };
  provider.addEventListener("change", syncLink);
  // הדביקו מפתח — הספק נבחר לפיו (Gemini מתחיל ב-AIza, Groq ב-gsk_)
  key.addEventListener("input", () => {
    const k = key.value.trim();
    const want = k.startsWith("AIza") ? "gemini" : k.startsWith("gsk_") ? "groq" : "";
    if (want && provider.value !== want) { provider.value = want; syncLink(); toast(want === "gemini" ? "זוהה מפתח Gemini" : "זוהה מפתח Groq"); }
  });
  syncLink();
  const model = el("input", { class: "ltr", value: v.ai_model || "", placeholder: "אוטומטי — המודל המהיר העדכני" });
  const testOut = el("p", { class: "small" });
  const botName = el("input", { value: v.bot_name || "", maxlength: 30 });
  const markAi = el("input", { type: "checkbox", checked: v.mark_ai !== false, "aria-label": "סימון שהבוט ענה" });
  const learn = el("input", { type: "checkbox", checked: v.learn_from_replies !== false, "aria-label": "לומד מהתשובות שלך" });
  const askMe = el("input", { type: "checkbox", checked: v.ask_when_unsure !== false, "aria-label": "שואל אותך כשלא יודע" });
  const aiTag = el("input", { value: v.ai_tag || "", maxlength: 60, placeholder: "🤖 הודעה אוטומטית" });
  const labelName = el("input", { value: v.label_name || "בוט", maxlength: 30 });
  const autoNew = el("input", { type: "checkbox", checked: v.auto_new !== false, "aria-label": "לקוח חדש נכנס לרשימה" });
  const contacts = el("input", { type: "checkbox", checked: Boolean(v.include_contacts), "aria-label": "גם אנשי קשר שמורים" });
  const handoff = el("input", { type: "number", min: 1, max: 1440, value: v.handoff_minutes || 60, class: "ltr" });
  const hours = el("input", { class: "ltr", value: v.hours || "", placeholder: "13:00-15:00, 20:00-22:30" });
  const friday = el("input", { class: "ltr", value: v.friday_until || "", placeholder: "12:00" });
  const info = el("textarea", { rows: 16, value: v.business_info || "" });
  const toggleRow = (input, title, sub) => el("div", { class: "opt" }, el("label", { class: "switch" }, input, el("span")),
    el("div", { class: "txt" }, el("div", { class: "title" }, title), sub ? el("div", { class: "muted small" }, sub) : null));

  const save = async () => {
    await attempt(() => api("/v1/admin/wabot", { method: "PUT", body: {
      enabled: enabled.checked, ai_provider: provider.value, ai_key: key.value, ai_model: model.value,
      bot_name: botName.value, label_name: labelName.value, auto_new: autoNew.checked, include_contacts: contacts.checked,
      mark_ai: markAi.checked, ai_tag: aiTag.value, learn_from_replies: learn.checked, ask_when_unsure: askMe.checked,
      handoff_minutes: Number(handoff.value), hours: hours.value, friday_until: friday.value, business_info: info.value } }),
    "נשמר — הבוט יקלוט תוך כ-15 שניות");
    render();
  };
  const testKey = async (btn) => {
    if (key.value.trim()) {
      toast("קודם שומרים את המפתח — ואז בודקים", true);
      return;
    }
    btn.disabled = true;
    testOut.className = "small muted";
    testOut.textContent = "בודק…";
    try {
      const r = await api("/v1/admin/wabot/test-ai", { method: "POST" });
      testOut.className = r.ok ? "small ok-text" : "error small";
      testOut.textContent = r.message;
    } catch (e) {
      testOut.className = "error small";
      testOut.textContent = e.message;
    } finally {
      btn.disabled = false;
    }
  };

  return [
    card("חיבור", "הבוט רץ בשרת ומחובר לוואטסאפ שלך כמו וואטסאפ ווב. כאן רואים את המצב, ואת ה-QR לחיבור.", statusBox),
    card("הבוט", null,
      toggleRow(enabled, "הבוט פעיל", "כבוי = מתנתק מהוואטסאפ ולא עונה לאף אחד."),
      el("div", { class: "grid two" },
        el("label", {}, "המוח (AI)", provider),
        el("label", {}, "מפתח", key),
        el("label", {}, "שם הבוט (איך הוא מציג את עצמו)", botName),
        el("label", {}, "מודל (לא חובה)", model)),
      el("div", { class: "row gap wrap" }, keyLink,
        el("span", { class: "muted small" }, "נכנסים עם חשבון גוגל → Create API key → מעתיקים ומדביקים כאן → שמירה → בדיקת מפתח.")),
      testOut,
      toggleRow(markAi, "סימון שהבוט ענה (כמו הסימון של וואטסאפ)", "בסוף כל הודעה אוטומטית מתווסף הסימון שלמטה — כך אתה (והלקוח) רואים שזו תשובה של הבוט."),
      el("label", {}, "הסימון", aiTag),
      el("div", { class: "row end gap" },
        v.key_set ? el("button", { class: "danger small", onclick: async () => {
          if (!await confirmDialog("למחוק את המפתח?", "הבוט יענה תשובה קבועה ויעביר אליך עד שתזינו מפתח חדש.", "מחיקה", true)) return;
          await attempt(() => api("/v1/admin/wabot", { method: "PUT", body: { clear_key: true } }), "נמחק");
          render();
        } }, "מחיקת המפתח") : null,
        el("button", { class: "ghost", onclick: (ev) => testKey(ev.currentTarget) }, "בדיקת מפתח"),
        el("button", { class: "primary", onclick: save }, "שמירה"))),
    card("ניהול מתוך הוואטסאפ", "בוואטסאפ ביזנס: רשימה (תווית) — השיחות שבה הן של הבוט. בלי לפתוח את הדשבורד.",
      el("ol", { class: "small steps" },
        el("li", {}, `לקוח חדש כותב → הבוט מוסיף אותו לרשימה "${v.label_name || "בוט"}" ועונה לו.`),
        el("li", {}, "נכנסת לשיחה וכתבת בעצמך → הבוט מוציא אותה מהרשימה וזז הצידה."),
        el("li", {}, `להחזיר את הבוט: בשיחה → הכפתור של הרשימות למעלה → מסמנים "${v.label_name || "בוט"}" → סיום. אם הלקוח מחכה לתשובה — הבוט עונה לו.`),
        el("li", {}, "לקוח ביקש נציג → הבוט מוציא את השיחה מהרשימה ושולח לך סיכום."),
        el("li", {}, "תפריט מהיר: כותבים \"בוט\" בצ'אט עם עצמך (עצירה לכולם, סטטוס, ועוד)."),
        el("li", {}, "פניות מצ'אט התמיכה באפליקציה מגיעות לצ'אט עם עצמך — \"השב\" עליהן והתשובה מגיעה ללקוח באפליקציה.")),
      el("div", { class: "grid two" },
        el("label", {}, "שם הרשימה בוואטסאפ", labelName),
        el("label", {}, "בלי רשימה (וואטסאפ רגיל): כמה דקות הבוט שותק אחרי שכתבת", handoff)),
      toggleRow(autoNew, "לקוח חדש נכנס לרשימה אוטומטית", "\"חדש\" = מעולם לא כתבת לו. שיחות קיימות שלך — הבוט לא נוגע."),
      toggleRow(contacts, "גם אנשי קשר שמורים בטלפון", "כבוי (מומלץ): משפחה וחברים ששמורים אצלך לא יקבלו תשובות מהבוט."),
      el("p", { class: "muted small" }, "אם הפעלת בוואטסאפ ביזנס עוזר AI מובנה (הודעות שמסומנות AI) — כבה אותו, כדי שלא יענו שניים."),
      el("div", { class: "row end" }, el("button", { class: "primary", onclick: save }, "שמירה"))),
    card("מה הבוט יודע", "כל מה שכתוב כאן — הבוט משתמש בו בתשובות: מחירים, מסלולים, כתובת, שאלות נפוצות. שורה שמתחילה ב\"כתובת:\" — הבוט שולח גם קישור לוויז ולמפות.",
      info,
      el("div", { class: "grid two" },
        el("label", {}, "שעות פעילות (א'–ה')", hours),
        el("label", {}, "שישי עד (שבת — סגור)", friday)),
      el("p", { class: "muted small" }, "בשעות הפעילות, אם אתה פעיל בוואטסאפ, הבוט מחכה 5 שניות לפני שהוא עונה — שתספיק לענות בעצמך. מחוץ לשעות — עונה מיד ואומר מתי חוזרים."),
      el("div", { class: "row end" }, el("button", { class: "primary", onclick: save }, "שמירה"))),
    card("מצב לימוד — מה הבוט למד ממך", "מה שכתוב כאן גובר על המידע הכללי למעלה. הבוט משתמש רק בתוכן הכללי של כל תשובה — לא בפרטים של לקוח אחר.",
      toggleRow(learn, "לומד מהתשובות שלך",
        "ענית ללקוח בעצמך (בשיחה שהבוט טיפל בה או שהיא ברשימה) — הבוט זוכר את התשובה לשאלה של הלקוח, ובפעם הבאה עונה ככה. אישורים קצרים (\"אוקיי\"), \"רגע, בודק\" ותשובות עם מספר טלפון — לא נשמרים. על כל דבר שנלמד מגיעה לך הודעה בצ'אט עם עצמך, עם אפשרות לבטל."),
      toggleRow(askMe, "כשלא יודע — שואל אותך",
        "במקום לנחש: עונה ללקוח שהוא בודק וחוזר אליו, ושולח לך שאלה בצ'אט עם עצמך. \"השב\" על השאלה עם התשובה — הבוט שולח ללקוח (בניסוח שלו) וזוכר לפעם הבאה."),
      el("details", {}, el("summary", {}, "ניהול מהצ'אט עם עצמך בוואטסאפ"),
        el("ul", { class: "small steps" },
          el("li", {}, el("b", {}, "למד: "), "משלוח לכל הארץ תוך 3 ימי עסקים — עובדה חדשה."),
          el("li", {}, el("b", {}, "ש: "), "כמה עולה סינון? ", el("i", {}, "(שורה חדשה) "), el("b", {}, "ת: "), "250 ₪ — שאלה ותשובה."),
          el("li", {}, el("b", {}, "מה למדת"), " — הרשימה. ", el("b", {}, "שכח 12"), " — מחיקה של פריט 12."),
          el("li", {}, "בתפריט (\"בוט\"): 7 — מה למדתי, 8 — איך ללמד."))),
      el("div", { class: "row end gap" }, knowCount,
        el("button", { class: "ghost", onclick: loadKnowledge }, "רענון"),
        el("button", { class: "ghost", onclick: async () => { if (await editKnowledge(null)) loadKnowledge(); } }, "הוספה"),
        el("button", { class: "primary", onclick: save }, "שמירה")),
      knowBox),
    el("p", { class: "muted small" }, "שימו לב: זה חיבור לא-רשמי לוואטסאפ (כמו וואטסאפ ווב). הבוט עונה רק למי שכותב, בקצב אנושי, ומעביר אליך — אבל יש סיכון שוואטסאפ יגביל את המספר."),
  ];
}

async function viewSystem() {
  const o = await api("/v1/admin/overview");
  const sys = await api("/v1/admin/system").catch(() => null);
  const wa = await api("/v1/admin/whatsapp").catch(() => null);
  const gpu = await api("/v1/admin/gpu").catch(() => null);
  const branding = await api("/v1/admin/branding").catch(() => null);
  const ag = o.adguard;
  const CHECKS = { db: "מסד נתונים", redis: "Redis", engine: "מנוע תמונות", wireguard: "WireGuard", adguard: "AdGuard" };
  const m = (snap, key) => (snap && snap[key] !== undefined ? num(Math.round(snap[key])) : "—");
  const healthCard = sys ? card("בריאות השרת", "מתעדכן בכל טעינה. ניטור חיצוני: curl http://127.0.0.1:8090/ready ו-/metrics (מתוך השרת).",
    el("div", { class: "chips" }, Object.entries(sys.checks).map(([k, ok]) => chip(`${CHECKS[k] || k}: ${ok ? "תקין" : "תקלה"}`, ok ? "ok" : "bad"))),
    el("div", { class: "grid two" },
      kv([
        ["תמונות במנוע (מאז ההפעלה)", m(sys.engine, 'fp_engine_jobs_total{action="pass"}') + " עברו · " + m(sys.engine, 'fp_engine_jobs_total{action="block"}') + " נחסמו"],
        ["נכשלו בגלל עומס / זמן", `${m(sys.engine, 'fp_engine_jobs_failed_total{reason="timeout"}')} / ${m(sys.engine, 'fp_engine_jobs_failed_total{reason="overload"}')}`],
        ["בעיבוד / בתור עכשיו", `${m(sys.engine, "fp_engine_active_jobs")} / ${m(sys.engine, "fp_engine_waiting_jobs")}`],
        ["זיכרון המנוע", sys.engine ? `${Math.round((sys.engine.fp_engine_memory_bytes || 0) / 1e6)}MB` : "—"],
        ["נותחו ב-GPU (Modal) / חזרו לשרת", `${m(sys.engine, 'fp_engine_remote_total{result="ok"}')} / ${m(sys.engine, 'fp_engine_remote_total{result="fallback"}')}`],
      ]),
      kv([
        ["זיכרון הפרוקסי", sys.proxy ? `${Math.round((sys.proxy.fp_proxy_memory_bytes || 0) / 1e6)}MB` : "—"],
        ["מפסק למנוע", sys.proxy ? (sys.proxy.fp_proxy_engine_circuit_open ? chip("פתוח — המנוע לא עונה", "bad") : chip("סגור", "ok")) : "—"],
        ["מכשירים: ירוק / צהוב / אדום", `${sys.fleet.states.green || 0} / ${sys.fleet.states.yellow || 0} / ${sys.fleet.states.red || 0}`],
        ["אירועים ב-24 שעות", Object.entries(sys.fleet.events_24h).map(([k, v]) => `${(LOG_KINDS[k] || ["", k])[1]}: ${v}`).join(" · ") || "—"],
      ]))) : null;
  const agCard = card("חסימות DNS לפי מכשיר (AdGuard)", "כל חסימה של אתר/שירות נאכפת גם בפרוקסי (מיד) וגם ב-DNS — כדי לתפוס גם חיבורים שאינם HTTPS.",
    kv([
      ["מצב", !ag.enabled ? chip("לא מוגדר", "warn") : ag.error ? chip("שגיאה", "bad") : chip("פעיל", "ok")],
      ["כללים", num(ag.rules)], ["סנכרון אחרון", ago(ag.last_ok)], ag.error ? ["שגיאה", ag.error] : null,
    ]),
    !ag.enabled ? el("p", { class: "muted small" }, "להפעלה: בשרת, בקובץ /etc/filterphone/filterphone.env להוסיף FP_ADGUARD_USER ו-FP_ADGUARD_PASSWORD (המשתמש של ממשק AdGuard), ואז systemctl restart fp-api.") : null,
    el("div", { class: "row end" }, el("button", { class: "ghost", disabled: !ag.enabled, onclick: async () => {
      const r = await attempt(() => api("/v1/admin/adguard/sync", { method: "POST" }));
      toast(r.error ? `שגיאה: ${r.error}` : `סונכרן · ${r.rules} כללים`, Boolean(r.error));
      render();
    } }, "סנכרון עכשיו")));
  const rows = catalog.options.map((x) => el("tr", {},
    el("td", {}, el("b", {}, x.title), el("small", {}, catalog.categories.find((c) => c.id === x.category)?.title)),
    el("td", {}, el("div", { class: "chips" }, (x.mechanisms || []).map((m) => chip(MECH[m]?.[1] || m, MECH[m]?.[0])))),
    el("td", {}, x.hosts.length ? el("details", {}, el("summary", {}, `${x.hosts.length} אתרים`), el("div", { class: "ltr mono small" }, x.hosts.join(" · "))) : "—"),
    el("td", {}, x.apps.length || x.screens.length ? el("details", {}, el("summary", {}, `${x.apps.length} אפליקציות${x.screens.length ? ` · ${x.screens.length} מסכים` : ""}`),
      el("div", { class: "ltr mono small" }, [...x.apps, ...x.screens.map((s) => `${s.package}: ${s.match.join("/")}`)].join(" · "))) : "—")));
  return [
    healthCard,
    brandingCard(branding),
    gpuCard(gpu),
    whatsappCard(wa),
    el("div", { class: "grid two" }, agCard,
      card("מנוע הסינון", null, kv([["מצב", o.engine ? chip("פעיל", "ok") : chip("לא זמין", "bad")],
        ["תמונות שנבדקו היום", num(o.today.scanned)], ["נחסמו / נצבעו היום", `${num(o.today.block)} / ${num(o.today.replace)}`]]),
      el("p", { class: "muted small" }, "בעיות? בשרת: journalctl -u fp-engine -n 50 ו-journalctl -u fp-proxy -n 50"))),
    card("קטלוג החסימות", "כל האפשרויות שמופיעות במכשירים ובטופס ההרשמה באפליקציה, ואיך כל אחת נאכפת.",
      el("div", { class: "table-wrap" }, el("table", {}, el("thead", {}, el("tr", {}, el("th", {}, "אפשרות"), el("th", {}, "אכיפה"),
        el("th", {}, "ברשת"), el("th", {}, "בטלפון"))), el("tbody", {}, rows)))),
  ];
}

// ---------------------------------------------------------------- הפעלה

async function start() {
  $("#login").hidden = true;
  $("#app").hidden = false;
  try {
    catalog = catalog || await api("/v1/admin/catalog");
  } catch (e) {
    if (!token) return;
    toast(e.message, true);
    return;
  }
  render();
}

if (token) start(); else showLogin();
