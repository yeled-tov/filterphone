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
  devices: ["מכשירים", viewDevices],
  customers: ["לקוחות", viewCustomers],
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
  if (!list.length) return [tabs, card(null, null, empty(status === "pending" ? "אין בקשות שמחכות לאישור." : "אין בקשות."))];
  return [tabs, ...list.map((r) => requestCard(r))];
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
  const body = el("tbody");
  const draw = () => {
    const q = search.value.trim().toLowerCase();
    const rows = list.filter((d) => (!customerFilter || String(d.customer_id) === customerFilter)
      && (!status.value || d.status === status.value) && (!level.value || String(d.level) === level.value)
      && (!q || [d.name, d.label, d.vpn_ip, d.customer?.name, d.customer?.phone].some((x) => (x || "").toLowerCase().includes(q))));
    body.replaceChildren(...rows.map((d) => el("tr", { class: "click", onclick: () => { location.hash = `#/devices/${d.id}`; } },
      el("td", {}, el("b", {}, d.name), el("small", {}, [d.customer?.name, d.customer?.phone, d.vpn_ip].filter(Boolean).join(" · "))),
      el("td", {}, d.status === "active" ? el("span", { class: "row gap nowrap" }, el("span", { class: `dot ${d.online ? "on" : ""}` }),
        d.online ? "מחובר" : "לא מחובר") : statusChip(d.status)),
      el("td", {}, levelBadge(d.level)),
      el("td", {}, d.blocks.length ? el("div", { class: "chips" }, d.blocks.slice(0, 4).map((b) => chip(b.title)),
        d.blocks.length > 4 ? chip(`+${d.blocks.length - 4}`) : null) : el("span", { class: "muted" }, "—")),
      el("td", { class: "nowrap" }, `${num(d.today.block)} / ${num(d.today.replace)}`, el("small", {}, "נחסמו / נצבעו")),
      el("td", { class: "nowrap" }, ago(d.last_handshake || d.last_seen_at)),
      el("td", {}, d.customer ? paymentChip(d.customer.payment) : null))));
    if (!rows.length) body.replaceChildren(el("tr", {}, el("td", { colspan: 7 }, empty("לא נמצאו מכשירים."))));
  };
  [search, status, level].forEach((x) => x.addEventListener("input", draw));
  draw();
  autoRefresh(async () => { const fresh = await api("/v1/admin/devices"); list.splice(0, list.length, ...fresh); draw(); }, 20000);
  topAction("רענון", render);
  return [
    el("div", { class: "row gap" }, el("div", { class: "grow" }, search), status, level,
      customerFilter ? el("a", { href: "#/devices" }, el("button", { class: "ghost small" }, "הצג את כל הלקוחות")) : null),
    el("section", { class: "card" }, el("div", { class: "table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {}, el("th", {}, "מכשיר"), el("th", {}, "מצב"), el("th", {}, "רמה"), el("th", {}, "חסימות"),
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

  // --- כותרת
  const header = el("section", { class: "card" },
    el("div", { class: "req-top" },
      el("span", { class: "avatar" }, d.name.slice(0, 1)),
      el("div", { class: "grow stack tight" },
        el("div", { class: "row gap" }, el("h2", {}, d.name), statusChip(d.status),
          active ? el("span", { class: "row gap nowrap small" }, el("span", { class: `dot ${d.online ? "on" : ""}` }),
            d.online ? "מחובר עכשיו" : `לא מחובר · ${ago(d.last_handshake || d.last_seen_at)}`) : null),
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
  const apps = ta("custom_apps", "com.example.game");
  const customCard = card("אתרים ואפליקציות — ידני", "חסימה של כל אתר או אפליקציה שלא ברשימה, ורשימת היתר (מסירה חסימה מאתר מסוים).",
    el("div", { class: "grid three" },
      el("label", {}, "אתרים לחסימה", hosts),
      el("label", {}, "אתרים מותרים (חריגים)", allow),
      el("label", {}, "אפליקציות לחסימה (שם חבילה)", apps)),
    el("div", { class: "row end" }, el("button", { class: "primary", onclick: async () => {
      await patch({ custom_hosts: lines(hosts.value), allow_hosts: lines(allow.value), custom_apps: lines(apps.value) });
      render();
    } }, "שמירה")));

  // --- האפליקציות בטלפון
  const appsCard = phoneAppsCard(d, patch);

  // --- מצב ההגנה בטלפון
  const g = d.guard || {};
  const yesNo = (v, good = true) => v === undefined ? chip("לא דווח") : chip(v ? "כן" : "לא", v === good ? "ok" : "bad");
  const guardCard = card("ההגנה בטלפון", d.guard_at ? `דיווח אחרון ${ago(d.guard_at)}` : "הטלפון עוד לא דיווח (גרסת אפליקציה ישנה?)", kv([
    ["מגן FilterPhone (נגישות)", yesNo(g.accessibility)],
    ["מנהל מכשיר (Device Owner)", yesNo(g.device_owner)],
    ["VPN תמיד מופעל", yesNo(g.always_on)],
    ["חסימת חיבורים בלי VPN", yesNo(g.lockdown)],
    ["תעודת סינון מותקנת", yesNo(g.ca_installed)],
    ["DNS פרטי (עוקף)", g.private_dns === undefined ? chip("לא דווח") : chip(g.private_dns === "hostname" ? "מופעל — עוקף!" : "כבוי", g.private_dns === "hostname" ? "bad" : "ok")],
  ]));

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
    el("div", { class: "grid two" }, customerCard, guardCard),
    levelCard, policyCard, appImagesCard, customCard, appsCard,
    el("div", { class: "grid two" }, statsCard, learnedCard),
    eventsCard, historyCard];
}

function phoneAppsCard(d, patch) {
  if (!d.apps.length) {
    return card("האפליקציות בטלפון", null, empty("הטלפון עוד לא שלח את רשימת האפליקציות. היא נשלחת אוטומטית מהאפליקציה (גרסה חדשה)."));
  }
  const custom = new Set(d.settings.custom_apps);
  const search = el("input", { type: "search", placeholder: "חיפוש אפליקציה..." });
  const list = el("div", { class: "apps" });
  const draw = () => {
    const q = search.value.trim().toLowerCase();
    list.replaceChildren(...d.apps.filter((a) => !q || a.label.toLowerCase().includes(q) || a.package.includes(q)).map((a) => {
      const viaOption = a.blocked && !custom.has(a.package);
      const input = el("input", { type: "checkbox", checked: a.blocked, disabled: viaOption, "aria-label": a.label,
        onchange: async (e) => {
          const on = e.target.checked;
          if (on) custom.add(a.package); else custom.delete(a.package);
          try {
            await patch({ custom_apps: [...custom] }, on ? `${a.label} נחסמה` : `${a.label} שוחררה`);
            a.blocked = on;
          } catch (_) {
            e.target.checked = !on;
            if (on) custom.delete(a.package); else custom.add(a.package);
          }
        } });
      return el("div", { class: "app-row" },
        el("label", { class: "switch" }, input, el("span")),
        el("div", { class: "grow" }, el("b", {}, a.label || a.package), el("small", {}, a.package)),
        viaOption ? chip("חסום דרך אפשרות", "violet") : a.system ? chip("מערכת") : null);
    }));
  };
  search.addEventListener("input", draw);
  draw();
  return card("האפליקציות בטלפון", `${d.apps.length} אפליקציות · עודכן ${ago(d.apps_at)} · מתג = חסימה בטלפון (דורש מגן FilterPhone)`, search, list);
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
        seg ? el("p", { class: "muted small" }, `סגמנטציה: עור גלוי ${(seg.skin * 100).toFixed(1)}% משטח האדם · אדם ${(seg.person * 100).toFixed(1)}% מהתמונה · ${seg.crops} חיתוכים`) : null,
        i.detections?.length ? el("div", { class: "chips" }, i.detections.map(([label, score]) => chip(`${label} ${score}`))) : null),
      card(`מה הטלפון מקבל ברמה ${lv}`, null, r.result_image
        ? el("img", { src: r.result_image, class: "result-img", alt: "התוצאה אחרי הסינון" })
        : el("p", { class: "decision pass" }, "התמונה עוברת כמו שהיא ברמה הזו."))),
  ];
}

// ---------------------------------------------------------------- קודים

async function viewCodes() {
  const list = await api("/v1/admin/codes");
  const form = el("form", { class: "grid three" },
    el("label", {}, "שם הלקוח", el("input", { name: "label", placeholder: "למשל: משפחת כהן" })),
    el("label", {}, "רמה", el("select", { name: "level" }, catalog.levels.filter((l) => l.level > 0).map((l) => el("option", { value: l.level }, `${l.level} · ${l.name}`)))),
    el("label", {}, "מספר מכשירים", el("input", { name: "uses", type: "number", min: 1, max: 100, value: 1 })),
    el("label", {}, "תוקף (ימים)", el("input", { name: "expires_days", type: "number", min: 1, max: 365, value: 30 })),
    el("div", { class: "row end" }, el("button", { class: "primary", type: "submit" }, "צור קוד")));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(form);
    const r = await attempt(() => api("/v1/admin/codes", { method: "POST", body: {
      label: f.get("label"), level: Number(f.get("level")), uses: Number(f.get("uses")), expires_days: Number(f.get("expires_days")),
    } }));
    await dialog("קוד ההפעלה", el("div", { class: "stack" }, el("p", { class: "code" }, r.code),
      el("p", { class: "muted small" }, "מוצג פעם אחת בלבד. שולחים ללקוח יחד עם קישור ההורדה."),
      el("button", { type: "button", class: "ghost", onclick: () => navigator.clipboard.writeText(r.code).then(() => toast("הועתק")) }, "העתקה")), "סגירה");
    render();
  });
  return [
    card("קוד הפעלה חדש", "הדרך המהירה בלי טופס הרשמה: הלקוח מזין קוד באפליקציה ומתחבר מיד ברמה שבחרת.", form),
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

async function viewSystem() {
  const o = await api("/v1/admin/overview");
  const ag = o.adguard;
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
