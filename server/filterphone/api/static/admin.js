// ממשק הניהול של FilterPhone. בלי ספריות; כל טקסט מהשרת נכנס דרך textContent (בלי innerHTML).
"use strict";

const LEVELS = { 0: "כבוי", 1: "מחמיר", 2: "בינוני", 3: "בסיסי" };
const ACTIONS = { block: "נחסם", replace: "נצבע" };
let token = sessionStorage.getItem("fp_token") || "";
let latestRequestCode = "";

const $ = (sel) => document.querySelector(sel);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null) node.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    ...opts,
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (res.status === 401) { showLogin(); throw new Error("נדרשת התחברות"); }
  if (res.status === 204) return null;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `שגיאה ${res.status}`);
  return data;
}

function ago(ts) {
  if (!ts) return "—";
  const s = Math.floor(Date.now() / 1000) - ts;
  if (s < 90) return "עכשיו";
  if (s < 3600) return `לפני ${Math.round(s / 60)} דק'`;
  if (s < 86400) return `לפני ${Math.round(s / 3600)} שע'`;
  return `לפני ${Math.round(s / 86400)} ימים`;
}

function date(ts) {
  return ts ? new Date(ts * 1000).toLocaleDateString("he-IL") : "ללא";
}

function levelSelect(value, onchange) {
  return el("select", { onchange },
    Object.entries(LEVELS).map(([k, v]) => {
      const o = el("option", { value: k }, `${k} — ${v}`);
      if (Number(k) === value) o.selected = true;
      return o;
    }));
}

// ---------------------------------------------------------------- התחברות

function showLogin() {
  token = "";
  sessionStorage.removeItem("fp_token");
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
    start();
  } catch (err) {
    $("#login-error").textContent = err.message;
  }
});

$("#logout").addEventListener("click", async () => {
  try { await api("/v1/admin/logout", { method: "POST" }); } catch (_) { /* ממילא יוצאים */ }
  showLogin();
});

// ---------------------------------------------------------------- לשוניות

document.querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => {
  document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x === b));
  document.querySelectorAll(".panel").forEach((p) => { p.hidden = p.dataset.panel !== b.dataset.tab; });
  refresh();
}));

function currentTab() {
  return document.querySelector(".tab.active").dataset.tab;
}

// ---------------------------------------------------------------- נתונים

async function loadStats() {
  const o = await api("/v1/admin/overview");
  const box = $("#stats");
  box.replaceChildren(
    el("div", { class: "stat" }, el("b", {}, o.devices), el("span", {}, "מכשירים")),
    el("div", { class: "stat" }, el("b", {}, o.online), el("span", {}, "מחוברים עכשיו")),
    el("div", { class: "stat" }, el("b", {}, o.today.block || 0), el("span", {}, "תמונות נחסמו היום")),
    el("div", { class: "stat" }, el("b", {}, o.today.replace || 0), el("span", {}, "תמונות נצבעו היום")),
  );
}

async function loadDevices() {
  const list = await api("/v1/admin/devices");
  $("#devices-empty").hidden = list.length > 0;
  $("#devices").replaceChildren(...list.map((d) => {
    const state = d.status !== "active" ? ["off", "מושבת"] : d.online ? ["on", "מחובר"] : ["", "לא מחובר"];
    const update = async (body) => {
      try { await api(`/v1/admin/devices/${d.id}`, { method: "PATCH", body }); refresh(); }
      catch (err) { alert(err.message); }
    };
    return el("tr", {},
      el("td", {}, d.name, el("small", {}, [d.label, d.platform, d.vpn_ip].filter(Boolean).join(" · "))),
      el("td", {}, el("span", { class: `dot ${state[0]}` }), state[1]),
      el("td", {}, levelSelect(d.level, (e) => update({ level: Number(e.target.value) }))),
      el("td", {}, `${d.today.block || 0} נחסמו`, el("small", {}, `${d.today.replace || 0} נצבעו`)),
      el("td", {}, ago(d.last_handshake || d.last_seen_at)),
      el("td", {}, el("div", { class: "actions" },
        el("button", { class: "ghost", onclick: () => update({ status: d.status === "active" ? "disabled" : "active" }) },
          d.status === "active" ? "השבת" : "הפעל"),
        el("button", { class: "ghost", title: "מאפס את רשימת האפליקציות שעוברות בלי בדיקה", onclick: async () => {
          const r = await api(`/v1/admin/devices/${d.id}/reset-apps`, { method: "POST" });
          alert(`אופסו ${r.cleared} כתובות`);
        } }, "איפוס אפליקציות"),
        el("button", { class: "danger", onclick: async () => {
          if (!confirm(`למחוק את "${d.name}"? המכשיר יתנתק מה-VPN ויצטרך קוד הפעלה חדש.`)) return;
          await api(`/v1/admin/devices/${d.id}`, { method: "DELETE" });
          refresh();
        } }, "מחק"),
      )),
    );
  }));
}

async function loadRequests() {
  const list = await api("/v1/admin/requests");
  $("#requests-empty").hidden = list.length > 0;
  $("#requests").replaceChildren(...list.map((r) => {
    const status = { pending: "ממתינה לטיפול", approved: "אושרה", rejected: "נדחתה" }[r.status] || r.status;
    const actions = r.status === "pending" ? el("div", { class: "actions" },
      el("button", { onclick: async () => {
        if (!confirm(`לאשר את בקשת ${r.name} וליצור קוד הפעלה לרמה ${r.level_name}?`)) return;
        try {
          const result = await api(`/v1/admin/requests/${r.id}/decision`, { method: "POST", body: { approved: true } });
          latestRequestCode = result.code;
          $("#request-code-value").textContent = result.code;
          $("#request-code-details").textContent = `${result.label} · ${result.level_name} · תוקף עד ${date(result.expires_at)}`;
          $("#request-code").hidden = false;
          refresh();
        } catch (err) { alert(err.message); }
      } }, "אשר וצור קוד"),
      el("button", { class: "danger", onclick: async () => {
        if (!confirm(`לדחות את בקשת ${r.name}?`)) return;
        try { await api(`/v1/admin/requests/${r.id}/decision`, { method: "POST", body: { approved: false } }); refresh(); }
        catch (err) { alert(err.message); }
      } }, "דחה"),
      el("button", { class: "danger", onclick: async () => {
        if (!confirm(`למחוק את פרטי בקשת ${r.name}?`)) return;
        try { await api(`/v1/admin/requests/${r.id}`, { method: "DELETE" }); refresh(); }
        catch (err) { alert(err.message); }
      } }, "מחק פרטים")) : el("div", {}, status,
      el("button", { class: "danger", onclick: async () => {
        if (!confirm(`למחוק את פרטי בקשת ${r.name}? קוד ההפעלה שכבר נוצר לא יימחק.`)) return;
        try { await api(`/v1/admin/requests/${r.id}`, { method: "DELETE" }); refresh(); }
        catch (err) { alert(err.message); }
      } }, "מחק פרטים"));
    return el("tr", {},
      el("td", { class: "ltr" }, r.reference),
      el("td", {}, r.name),
      el("td", {}, r.email, el("small", { class: "ltr" }, r.phone)),
      el("td", {}, `${r.level} — ${r.level_name}`),
      el("td", {}, new Date(r.created_at * 1000).toLocaleString("he-IL")),
      el("td", {}, actions),
    );
  }));
}

async function loadCodes() {
  const list = await api("/v1/admin/codes");
  $("#codes").replaceChildren(...list.map((c) => el("tr", {},
    el("td", {}, c.label || "—"),
    el("td", { class: "ltr" }, `…${c.hint}`),
    el("td", {}, LEVELS[c.level]),
    el("td", {}, c.uses_left),
    el("td", {}, date(c.expires_at)),
    el("td", {}, el("div", { class: "actions" }, el("button", { class: "danger", onclick: async () => {
      if (!confirm("לבטל את הקוד?")) return;
      await api(`/v1/admin/codes/${c.id}`, { method: "DELETE" });
      refresh();
    } }, "בטל"))),
  )));
}

async function loadEvents() {
  const list = await api("/v1/admin/events?limit=200");
  $("#events").replaceChildren(...list.map((e) => el("tr", {},
    el("td", {}, new Date(Number(e.ts) * 1000).toLocaleString("he-IL")),
    el("td", {}, e.device),
    el("td", { class: "ltr" }, e.host),
    el("td", {}, ACTIONS[e.action] || e.action),
    el("td", { class: "ltr" }, e.reason),
  )));
}

$("#level-select").replaceChildren(...Object.entries(LEVELS).filter(([k]) => k !== "0")
  .map(([k, v]) => el("option", { value: k }, `${k} — ${v}`)));

$("#code-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  try {
    const r = await api("/v1/admin/codes", { method: "POST", body: {
      label: f.get("label"), level: Number(f.get("level")),
      uses: Number(f.get("uses")), expires_days: Number(f.get("expires_days")),
    } });
    $("#new-code-value").textContent = r.code;
    $("#new-code").hidden = false;
    e.target.reset();
    refresh();
  } catch (err) { alert(err.message); }
});

$("#copy-code").addEventListener("click", () => navigator.clipboard.writeText($("#new-code-value").textContent));
$("#copy-request-code").addEventListener("click", () => navigator.clipboard.writeText(latestRequestCode));

async function refresh() {
  try {
    await loadStats();
    const tab = currentTab();
    if (tab === "requests") await loadRequests();
    if (tab === "devices") await loadDevices();
    if (tab === "codes") await loadCodes();
    if (tab === "events") await loadEvents();
  } catch (err) {
    console.error(err);
  }
}

function start() {
  $("#login").hidden = true;
  $("#app").hidden = false;
  refresh();
}


document.addEventListener("visibilitychange", () => { if (!document.hidden && token) refresh(); });
if (token) start(); else showLogin();
setInterval(() => { if (token && !document.hidden) refresh(); }, 15000);
