'use strict';
/*
 * בוט הוואטסאפ של FilterPhone (whatsapp-web.js) — רץ בשרת כשירות fp-wabot.
 *
 * הכול מנוהל מהדשבורד (מערכת → בוט וואטסאפ): הפעלה/כיבוי, QR לחיבור, מוח ה-AI (Gemini / Groq),
 * מידע העסק ושעות הפעילות. הבוט מושך את ההגדרות מהשרת ומדווח לשם את המצב שלו.
 *
 * מה הוא עושה בוואטסאפ שלך:
 *   • עונה ללקוחות ולמתעניינים — אנושי, לפי מידע העסק. בשעות הפעילות, אם אתה פעיל, מחכה 5 שניות
 *     כדי שתספיק לענות בעצמך.
 *   • ניהול מתוך הוואטסאפ (וואטסאפ ביזנס): רשימה בשם "בוט". לקוח חדש — הבוט מוסיף אותו לרשימה ועונה;
 *     כתבת בעצמך בשיחה — הבוט מוציא אותה מהרשימה וזז הצידה; להחזיר — בשיחה, הכפתור למעלה → "בוט".
 *     (בלי הרשימה: כתבת ללקוח → הבוט שותק בשיחה לזמן שנקבע בדשבורד.) ביקשו נציג → מעביר אליך עם סיכום.
 *   • פניות מצ'אט התמיכה באפליקציה מגיעות אליך לצ'אט עם עצמך; "השב" עליהן או "#מכשיר טקסט" — חוזר
 *     ללקוח באפליקציה.
 *   • תפריט ניהול: כתוב "בוט" בצ'אט עם עצמך (כמו בבוט הקודם).
 *   • לקוח קיים (לפי הטלפון): חסימה — מבצע לבד; פעולה רגישה (השהיה/פתיחה) — רק נרשמת לאישורך.
 *
 * אזהרה: API לא-רשמי של וואטסאפ — יש סיכון לחסימת המספר. הבוט מגיב בלבד, בקצב סביר, ומוסר אליך.
 */
const fs = require('fs');
const path = require('path');
const patched = require('./lib/wwebjs_patch').apply();     // לפני require('whatsapp-web.js') — ראו שם
const { Client, LocalAuth } = require('whatsapp-web.js');
const { State } = require('./lib/state');
const fp = require('./lib/fp');
const ai = require('./lib/ai');
const { israelNow, isOpen } = require('./lib/hours');
const { replyDelayMs, typingMs, parseHashReply, waNumber } = require('./lib/logic');
const labels = require('./lib/labels');

const VERSION = '2.1.0';
const API = (process.env.FP_API || 'http://127.0.0.1:8090').replace(/\/$/, '');
const TOKEN = process.env.FP_WA_BOT_TOKEN || process.env.FP_BOT_TOKEN || '';
const DATA = process.env.FP_WABOT_DATA || path.join(__dirname, 'data');
const CONFIG_EVERY_MS = 15000;
const OUTBOX_EVERY_MS = 5000;
const ADMIN_ACTIVE_MS = 2 * 60e3;
const READ_CHECK_MS = 3000;
const LABEL_EVERY_MS = 60e3;
const CATCHUP_EVERY_MS = 20e3;
const READY_TIMEOUT_MS = 2 * 60e3;   // "מחובר" בוואטסאפ אבל לא סיים לטעון — אחרי זה מאתחלים (בלי סריקה)
const CALL_TIMEOUT_MS = 20e3;        // קריאה לדף של וואטסאפ ווב שלא חוזרת — לא תוקעת את הבוט

// יומן קצר (40 שורות אחרונות) — נשלח לדשבורד עם המצב, כדי לראות מה קורה בלי גישה לשרת
const LOG = [];
for (const level of ['log', 'warn', 'error']) {
  const orig = console[level].bind(console);
  console[level] = (...args) => {
    orig(...args);
    const t = new Date().toLocaleTimeString('he-IL', { timeZone: 'Asia/Jerusalem', hour12: false });
    const line = args.map((a) => (a instanceof Error ? a.message : typeof a === 'string' ? a : JSON.stringify(a))).join(' ');
    LOG.push(`${t} ${level === 'log' ? '' : level === 'warn' ? '⚠ ' : '✖ '}${line}`.slice(0, 300));
    if (LOG.length > 40) LOG.shift();
  };
}

/** הבטחה שלא חוזרת בזמן → שגיאה (במקום שהבוט ייתקע לנצח על קריאה לדף). */
function withTimeout(p, ms = CALL_TIMEOUT_MS, what = 'call') {
  let t;
  return Promise.race([p, new Promise((_, rej) => { t = setTimeout(() => rej(new Error(`${what}: timeout`)), ms); })])
    .finally(() => clearTimeout(t));
}

if (!TOKEN) {
  console.error('חסר FP_WA_BOT_TOKEN (נוצר אוטומטית בהתקנת השרת — deploy/install.sh).');
  process.exit(1);
}

const srv = fp.client(API, TOKEN);
const state = new State(path.join(DATA, 'state.json'));
let cfg = null;               // ההגדרות מהדשבורד
let client = null;            // מחובר רק כשהבוט מופעל
let status = { state: 'off' };
let me = '';                  // המספר שלי (972…@c.us)
let adminLastSeen = 0;
let lastOutboxId = 0;
const botMsgIds = new Set();  // הודעות שהבוט שלח — כדי לא להתייחס אליהן כאילו אתה כתבת
// אותו דבר לפי תוכן: וואטסאפ לפעמים מדווח על ההודעה לפני ש-sendMessage מחזיר את המזהה
const outgoing = new Map();   // טקסט → עד מתי לצפות לו
const appThreads = new Map(); // הודעת העברה בצ'אט שלך → מכשיר (לתשובה ב"השב")
const pending = new Map();    // תשובות שמחכות (5 שניות בשעות הפעילות)
const stats = { replies: 0, handoffs: 0, app_forwarded: 0, app_replies: 0, ai_errors: 0, compared: 0 };
let menu = { open: false, step: '' };
let botLabel = null;          // הרשימה "בוט" בוואטסאפ ביזנס — כשקיימת, הבוט מנוהל לפיה
let labelState = '';          // on | missing | not_business
let labelCheckedAt = 0;
let readyTimer = null;
let readyFailures = 0;

// ------------------------------------------------------------------ מצב → לדשבורד

async function report(patch = {}) {
  status = { ...status, ...patch };
  try {
    await srv.status({ state: status.state, qr: status.qr || '', me: me.replace(/@.+/, ''), error: status.error || '',
      ai: status.ai || '', version: VERSION, stats, labels: labelState, label: botLabel?.name || cfg?.label_name || '',
      progress: status.state === 'starting' && Number.isFinite(status.progress) ? status.progress : null,
      log: LOG.slice(-30) });
  } catch (e) {
    console.warn('status report failed:', e.message);
  }
}

// ------------------------------------------------------------------ הגדרות: הפעלה / כיבוי

let configBusy = false;
let startFailures = 0;
let nextStartAt = 0;          // אחרי הפעלה שנכשלה — מחכים (דקה, 2, 4… עד 10) ולא מפעילים Chromium בלולאה

async function loadConfig() {
  if (configBusy) return;
  configBusy = true;
  try {
    try {
      cfg = await srv.config();
    } catch (e) {
      console.warn('config:', e.message);
      return;
    }
    if (cfg.enabled && !client) {
      if (Date.now() >= nextStartAt) await start();
      else await report();
    } else if (!cfg.enabled && client) await stop();
    else {
      if (client && status.state === 'ready' && Date.now() - labelCheckedAt > LABEL_EVERY_MS) await refreshLabel();
      await report();
    }
  } finally {
    configBusy = false;
  }
}

/**
 * הדפדפן: CHROME_PATH (מה שההתקנה מצאה). אם וואטסאפ ווב לא מצליח לטעון בו 3 פעמים ברצף — עוברים לבד
 * ל-CHROME_ALT (Chrome for Testing בגרסה קבועה, שההתקנה מורידה) ונשארים בו עד הפעלה מחדש של השירות.
 */
let useAlt = false;
let altBroken = false;
function chromePath() {
  const alt = process.env.CHROME_ALT;
  const altOk = Boolean(alt) && !altBroken && fs.existsSync(alt);
  if (!useAlt && altOk && readyFailures + startFailures >= 3) {
    useAlt = true;
    console.warn(`עובר ל-Chrome היציב (${alt}) אחרי ${readyFailures + startFailures} ניסיונות שלא הצליחו`);
  }
  if (useAlt && altOk) return alt;
  if (process.env.CHROME_PATH && fs.existsSync(process.env.CHROME_PATH)) return process.env.CHROME_PATH;
  if (altOk) return alt;
  return ['/usr/bin/google-chrome', '/usr/bin/chromium', '/snap/bin/chromium', '/usr/bin/chromium-browser']
    .find((p) => fs.existsSync(p));
}

/** נעילות של Chrome שנשארו מכיבוי לא מסודר — בלעדיהן Chrome מסרב לפתוח את אותו פרופיל. */
function clearProfileLocks() {
  const dir = path.join(DATA, 'auth', 'session-filterphone');
  for (const f of ['SingletonLock', 'SingletonSocket', 'SingletonCookie']) {
    try { fs.rmSync(path.join(dir, f), { force: true }); } catch { /* */ }
  }
}

async function start() {
  console.log(`מפעיל את הבוט… (${chromePath() || 'Chrome לא נמצא'}${patched ? ', תיקון ready פעיל' : ''})`);
  await report({ state: 'starting', qr: '', error: '', progress: null });
  clearProfileLocks();
  const c = new Client({
    authStrategy: new LocalAuth({ clientId: 'filterphone', dataPath: path.join(DATA, 'auth') }),
    // המטמון של גרסת וואטסאפ ווב — בתיקיית הנתונים של הבוט. ברירת המחדל (./.wwebjs_cache ליד הקוד) לא
    // ניתנת לכתיבה לשירות, והשגיאה נבלעת בספרייה — זו הסיבה שהבוט נתקע על "מתחבר".
    webVersionCache: { type: 'local', path: path.join(DATA, 'wwebjs_cache') },
    puppeteer: {
      headless: true,
      executablePath: chromePath(),
      args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage', '--disable-gpu',
        '--disable-extensions', '--no-first-run', '--no-zygote', '--password-store=basic',
        '--no-default-browser-check'],
      timeout: 180000,
      protocolTimeout: 180000,
    },
    qrMaxRetries: 0,
    authTimeoutMs: 180000,
    takeoverOnConflict: true,
    takeoverTimeoutMs: 60000,
  });
  c.on('qr', (qr) => { console.log('ממתין לסריקת QR (בדשבורד)'); report({ state: 'qr', qr, progress: null }); });
  c.on('loading_screen', (percent) => {
    if (client !== c) return;
    console.log(`וואטסאפ ווב נטען: ${percent}%`);
    report({ state: 'starting', progress: Number(percent) || 0 });
  });
  c.on('authenticated', () => {
    console.log('authenticated — החיבור לוואטסאפ אושר, ממתין לסיום הטעינה');
    withTimeout(c.getWWebVersion(), 10e3, 'version').then((v) => console.log('גרסת וואטסאפ ווב:', v)).catch(() => {});
    armReadyWatchdog(c);
    report({ state: 'starting', qr: '' });
  });
  c.on('auth_failure', (m) => { console.error('auth_failure:', m); report({ state: 'error', error: `אימות נכשל: ${m}` }); });
  c.on('change_state', (st) => console.log('מצב החיבור:', st));
  c.on('ready', () => {
    clearTimeout(readyTimer);
    readyFailures = 0;
    me = c.info?.wid?._serialized || '';
    console.log('מחובר ✓', me, cfg?.shadow ? '(מצב השוואה — לא עונה ללקוחות)' : '');
    status.state = 'ready';
    status.progress = null;
    refreshLabel().finally(() => report({ state: 'ready', qr: '', error: '' }));
  });
  c.on('disconnected', (r) => onDisconnected(c, r));
  c.on('message_create', (msg) => onMessage(msg).catch((e) => console.error('message:', e.message)));
  client = c;
  try {
    await c.initialize();
    startFailures = 0;
  } catch (e) {
    console.error('initialize failed:', e.message || String(e));
    clearTimeout(readyTimer);
    try { await withTimeout(c.destroy(), 30e3, 'destroy'); } catch { /* */ }
    if (client !== c) return;                     // כובה בינתיים מהדשבורד
    client = null;
    startFailures += 1;
    if (useAlt) {                                 // הדפדפן החלופי לא עלה — חוזרים לראשי
      useAlt = false;
      altBroken = true;
      console.warn('ה-Chrome החלופי לא עלה — חוזר ל-Chromium');
    }
    nextStartAt = Date.now() + Math.min(10 * 60e3, 60e3 * 2 ** (startFailures - 1));
    await report({ state: 'error', error: `הפעלה נכשלה: ${e.message || e}`.slice(0, 280) });
  }
}

/**
 * באג ידוע בספרייה: "authenticated" מגיע (בוואטסאפ רואים "מחובר") אבל "ready" לא — הבוט היה נתקע על
 * "מתחבר" לתמיד. אם תוך READY_TIMEOUT_MS לא הגיע ready — מכבים ומפעילים מחדש. החיבור שמור בתיקייה,
 * כך שאין צורך לסרוק QR שוב. ניסיונות חוזרים — בהמתנה עולה (15 שנ', 30, 60… עד 10 דק').
 */
function armReadyWatchdog(c) {
  clearTimeout(readyTimer);
  readyTimer = setTimeout(() => onReadyTimeout(c).catch((e) => console.error('watchdog:', e.message)), READY_TIMEOUT_MS);
}

async function onReadyTimeout(c) {
  if (client !== c || status.state === 'ready') return;
  readyFailures += 1;
  console.warn(`וואטסאפ מחובר אבל לא סיים לטעון תוך ${READY_TIMEOUT_MS / 60e3} דק' — מאתחל מחדש בלי סריקה (ניסיון ${readyFailures})`);
  client = null;
  me = '';
  try { await withTimeout(c.destroy(), 30e3, 'destroy'); } catch { /* */ }
  nextStartAt = Date.now() + Math.min(10 * 60e3, 15e3 * 2 ** (readyFailures - 1));
  await report({ state: 'error', progress: null,
    error: `וואטסאפ התחבר אבל לא סיים לטעון — הבוט מאתחל את עצמו (ניסיון ${readyFailures}). אין צורך לסרוק שוב.` });
}

/** נותק (התנתקת מהטלפון / וואטסאפ סגר את החיבור) — מכבים, ובסבב ההגדרות הבא מפעילים מחדש (QR חדש אם צריך). */
async function onDisconnected(c, reason) {
  console.warn('disconnected:', reason);
  if (client !== c) return;
  clearTimeout(readyTimer);
  client = null;
  me = '';
  try { await c.destroy(); } catch { /* */ }
  await report({ state: 'disconnected', qr: '', error: String(reason || '') });
}

async function stop() {
  console.log('מכבה את הבוט');
  clearTimeout(readyTimer);
  const c = client;
  client = null;
  me = '';
  try { await c.destroy(); } catch { /* */ }
  await report({ state: 'off', qr: '', error: '' });
}

// ------------------------------------------------------------------ הרשימה "בוט" (וואטסאפ ביזנס)

async function refreshLabel() {
  labelCheckedAt = Date.now();
  if (!client) return;
  if (!['smba', 'smbi'].includes(client.info?.platform)) { botLabel = null; labelState = 'not_business'; return; }
  try {
    botLabel = labels.findLabel(await withTimeout(client.getLabels(), CALL_TIMEOUT_MS, 'getLabels'), cfg?.label_name);
    labelState = botLabel ? 'on' : 'missing';
  } catch (e) {
    console.warn('labels:', e?.message || e);
  }
}

async function chatLabelIds(chatId) {
  return (await withTimeout(client.getChatLabels(chatId), CALL_TIMEOUT_MS, 'getChatLabels')).map((l) => String(l.id));
}

async function inBotList(chatId) {
  if (!botLabel || !client) return false;
  try { return (await chatLabelIds(chatId)).includes(String(botLabel.id)); } catch { return false; }
}

/** מוסיף/מוציא שיחה מהרשימה "בוט" (שאר הרשימות של השיחה נשארות). */
async function setBotList(chatId, on) {
  if (!botLabel || !client) return false;
  try {
    const ids = await chatLabelIds(chatId);
    const want = labels.withLabel(ids, botLabel.id, on);
    if (want.length !== ids.length || !want.every((x) => ids.includes(x))) await withTimeout(client.addOrRemoveLabels(want, [chatId]), CALL_TIMEOUT_MS, 'addOrRemoveLabels');
    return true;
  } catch (e) {
    console.warn('label change:', e?.message || e);
    return false;
  }
}

/** לקוח חדש? — מעולם לא כתבת לו, ולא איש קשר שמור (אלא אם ביקשת בדשבורד). */
async function looksNew(msg) {
  try {
    const chat = await msg.getChat();
    const msgs = await withTimeout(chat.fetchMessages({ limit: 40 }), CALL_TIMEOUT_MS, 'fetchMessages');
    let isContact = false;
    try { isContact = Boolean((await msg.getContact())?.isMyContact); } catch { /* */ }
    return labels.isNewChat(msgs, { isContact, includeContacts: Boolean(cfg?.include_contacts) });
  } catch {
    return false;
  }
}

/** החזרת את הבוט לשיחה (סימנת "בוט") והלקוח מחכה? — עונה לו. גם אחרי הפעלה מחדש של הבוט. */
async function catchUp() {
  try {
    if (client && status.state === 'ready' && botLabel && cfg?.enabled && !cfg.shadow && state.data.botActive) {
      for (const chat of await withTimeout(client.getChatsByLabelId(botLabel.id), CALL_TIMEOUT_MS, 'getChatsByLabelId')) {
        const chatId = chat?.id?._serialized;
        if (!chatId || chat.isGroup || pending.has(chatId) || chatId === adminChat()) continue;
        const last = chat.lastMessage;
        const c = state.chat(chatId);
        if (!labels.needsCatchUp(last, { answered: c.answered || '' })) continue;
        const text = last.body.trim();
        if (c.lastIn !== last.id._serialized) {
          state.remember(chatId, 'user', text);
          c.lastIn = last.id._serialized;
        }
        if (!c.phone) c.phone = await realPhone(last);
        c.known = true;
        schedule(last, chatId, c.phone, text, inHours(), 1500);
      }
    }
  } catch (e) {
    console.warn('catch-up:', e?.message || e);
  } finally {
    setTimeout(catchUp, CATCHUP_EVERY_MS);
  }
}

// ------------------------------------------------------------------ שליחה

async function send(to, text, { typing = false } = {}) {
  if (!client) return null;
  if (typing) {
    try {
      const chat = await client.getChatById(to);
      await chat.sendStateTyping();
      await sleep(typingMs(text));
      await chat.clearState();
    } catch { /* לא קריטי */ }
  }
  const key = String(text).trim();
  outgoing.set(key, Date.now() + 60e3);
  const m = await client.sendMessage(to, text);
  if (m?.id?._serialized) botMsgIds.add(m.id._serialized);
  return m;
}

/** ההודעה הזו נשלחה עכשיו ע"י הבוט עצמו (לפי מזהה או לפי תוכן)? */
function sentByBot(id, body) {
  if (id && botMsgIds.has(id)) { botMsgIds.delete(id); return true; }
  const now = Date.now();
  for (const [k, until] of outgoing) if (until < now) outgoing.delete(k);
  if (outgoing.has(body)) { outgoing.delete(body); return true; }
  return false;
}

/** לאן הבוט שולח לך הודעות: הצ'אט עם עצמך, או מספר המנהל מהגדרות התמיכה אם הוא מספר אחר. */
function adminChat() {
  const admin = String(cfg?.admin_number || '').replace(/\D/g, '');
  return admin && admin !== me.replace(/@.+/, '') ? `${admin}@c.us` : me;
}
const toAdmin = (text) => send(adminChat(), text);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ------------------------------------------------------------------ פניות מהאפליקציה → אליך

async function pollOutbox() {
  try {
    if (client && status.state === 'ready') {
      const { messages } = await srv.outbox(lastOutboxId);
      if (messages?.length) {
        const done = [];
        const waIds = {};
        for (const m of messages) {
          const head = `📩 *פנייה מהאפליקציה* · ${m.customer || 'לקוח'}${m.phone ? ' · ' + m.phone : ''} · מכשיר #${m.device_id}`;
          const sent = await toAdmin(`${head}\n\n${m.text}\n\n↩️ "השב" על ההודעה, או: #${m.device_id} <תשובה>`);
          if (sent?.id?._serialized) {
            waIds[m.id] = sent.id._serialized;
            appThreads.set(sent.id._serialized, m.device_id);
          }
          done.push(m.id);
          lastOutboxId = Math.max(lastOutboxId, m.id);
        }
        stats.app_forwarded += done.length;
        await srv.forwarded(done, waIds);
      }
    }
  } catch (e) {
    console.warn('outbox:', e.message);
  } finally {
    setTimeout(pollOutbox, OUTBOX_EVERY_MS);
  }
}

// ------------------------------------------------------------------ הודעות

async function onMessage(msg) {
  if (msg.from?.endsWith('@g.us') || msg.to?.endsWith('@g.us')) return;   // קבוצות
  if (msg.from === 'status@broadcast' || msg.isStatus) return;
  const body = (msg.body || '').trim();
  if (!body) return;
  if (msg.fromMe) {
    if (sentByBot(msg.id?._serialized, body)) return;                     // הבוט עצמו — לא "אתה כתבת"
    const selfChat = msg.to === me || msg.to === msg.from;
    return onAdmin(msg, body, selfChat);
  }
  if (adminChat() !== me && (msg.from === adminChat() || (await realPhone(msg)) === adminChat().replace(/@.+/, ''))) {
    return onAdmin(msg, body, true);                                     // אתה, ממספר המנהל
  }
  return onCustomer(msg, body);
}

/**
 * אתה כתבת. בצ'אט עם עצמך — תפריט / תשובה ללקוח באפליקציה. בשיחה עם לקוח — הבוט זז הצידה:
 * מוציא את השיחה מהרשימה "בוט" (או, בלי רשימה, שותק בה לזמן שנקבע).
 */
async function onAdmin(msg, body, selfChat) {
  if (!selfChat && cfg?.shadow) return onTheirReply(msg.to, body);   // מצב השוואה: זו התשובה "שלהם"
  adminLastSeen = Date.now();
  if (!selfChat) {
    const chatId = msg.to;
    clearTimeout(pending.get(chatId));
    pending.delete(chatId);
    const c = state.chat(chatId);
    c.known = true;
    state.remember(chatId, 'assistant', body);
    if (botLabel) await setBotList(chatId, false);
    else state.pause(chatId, (cfg?.handoff_minutes || 60) * 60e3);
    return;
  }
  // תשובה ללקוח באפליקציה: "#123 טקסט", או "השב" על הודעת פנייה
  let reply = parseHashReply(body);
  if (!reply && msg.hasQuotedMsg) {
    const q = await msg.getQuotedMessage().catch(() => null);
    const deviceId = q && appThreads.get(q.id._serialized);
    if (deviceId) reply = { deviceId, text: body };
  }
  if (reply) {
    try {
      await srv.reply(reply.deviceId, reply.text);
      stats.app_replies += 1;
      await msg.react('✅').catch(() => {});
    } catch (e) {
      await toAdmin(`⚠️ התשובה למכשיר #${reply.deviceId} לא נשלחה: ${e.message}`);
    }
    return;
  }
  await onMenu(body);
}

const MENU = `🛠️ *תפריט הבוט*

💡 בכל שיחה: הכפתור למעלה (רשימות) → *בוט* — מחזיר את הבוט לשיחה; כתבת בעצמך — הבוט זז הצידה.

*1* - 🔴 עצור את הבוט לכולם
*2* - 🟢 הפעל את הבוט לכולם
*3* - 📊 סטטוס ושיחות
*4* - ⏸️ עצור לקוח מסוים
*5* - ▶️ הפעל לקוח מסוים (גם אחרי "נציג")
*6* - 🔄 מחק היסטוריה של לקוח

*0* - ❌ סגור`;

async function onMenu(text) {
  if (text === 'בוט' || text.toLowerCase() === 'bot') { menu = { open: true, step: '' }; return toAdmin(MENU); }
  if (!menu.open) return;
  if (text === '0') { menu = { open: false, step: '' }; return toAdmin('✅ התפריט נסגר.'); }
  if (menu.step) {
    const phone = waNumber(text);
    if (phone.length < 10) return toAdmin('⚠️ מספר לא תקין. נסה שוב או *0*.');
    const chatId = state.findByPhone(phone) || `${phone}@c.us`;
    const step = menu.step;
    menu = { open: false, step: '' };
    if (step === 'pause') {
      if (botLabel) await setBotList(chatId, false);
      else state.pause(chatId, 365 * 24 * 3600e3);
      state.chat(chatId).known = true;
      return toAdmin(`🔴 הבוט עצור עבור ${phone}.`);
    }
    if (step === 'resume') {
      state.resume(chatId);
      if (botLabel && !(await setBotList(chatId, true))) return toAdmin(`⚠️ לא הצלחתי להוסיף את ${phone} לרשימה "${botLabel.name}".`);
      return toAdmin(`🟢 הבוט פעיל שוב עבור ${phone}.`);
    }
    if (step === 'reset') { state.reset(chatId); return toAdmin(`🔄 ההיסטוריה של ${phone} נמחקה.`); }
  }
  if (text === '1') { state.data.botActive = false; state.save(); menu.open = false; return toAdmin('🔴 *הבוט עצור לכולם* — כל הודעה תועבר אליך.'); }
  if (text === '2') { state.data.botActive = true; state.save(); menu.open = false; return toAdmin('🟢 *הבוט פעיל לכולם*'); }
  if (text === '3') {
    menu.open = false;
    const open = inHours();
    let list = [];
    if (botLabel) {
      list = (await withTimeout(client.getChatsByLabelId(botLabel.id), CALL_TIMEOUT_MS, 'getChatsByLabelId')
        .catch(() => [])).map((ch) => ch.id._serialized);
    }
    const lines = state.recent(10).map(([id, c], i) => {
      const st = botLabel ? (list.includes(id) ? '🤖 הבוט' : '👤 אתה') :
        state.paused(id) ? '👤 אתה מטפל' : c.handedOff ? '⏳ ממתין לנציג' : '🤖 הבוט';
      return `${i + 1}. ${c.phone || id.replace(/@.+/, '')} — ${st}`;
    });
    const mode = botLabel ? `📋 רשימת "${botLabel.name}": ${list.length} שיחות` :
      labelState === 'missing' ? `📋 אין רשימה בשם "${cfg?.label_name || labels.DEFAULT_NAME}" — צור אותה בוואטסאפ` :
        '⏱️ בלי רשימה: כתבת ללקוח → הבוט שותק שם לזמן קצוב';
    const shadow = cfg?.shadow ? `🔍 *מצב השוואה* — לא עונה ללקוחות, רק רושם בדשבורד (${stats.compared || 0} השוואות)\n` : '';
    return toAdmin(`📊 *סטטוס*\n${shadow}${state.data.botActive ? '🟢 פעיל' : '🔴 עצור'} · ${open ? '☀️ שעות פעילות' : '🌙 מחוץ לשעות'}\n${mode}\n` +
      `תשובות: ${stats.replies} · הועברו אליך: ${stats.handoffs}\n\n${lines.join('\n') || '_אין שיחות_'}`);
  }
  const steps = { 4: 'pause', 5: 'resume', 6: 'reset' };
  if (steps[text]) { menu.step = steps[text]; return toAdmin('📞 שלח את מספר הטלפון של הלקוח (למשל 0501234567), או *0*.'); }
  return toAdmin('❓ שלח מספר 1–6, או *0*.');
}

function inHours() {
  return isOpen(israelNow(), cfg?.hours || '', cfg?.friday_until || '');
}

/** מספר הטלפון האמיתי (גם כשוואטסאפ מזהה לפי @lid). */
async function realPhone(msg) {
  try {
    const c = await msg.getContact();
    if (/^\d{7,}$/.test(c?.number || '')) return c.number;
    if (/^\d{7,}$/.test(c?.id?.user || '')) return c.id.user;
  } catch { /* */ }
  return msg.from.replace(/@.+/, '');
}

async function adminReadSoon(notice) {
  if (!notice) return false;
  await sleep(READ_CHECK_MS);
  try {
    const chat = await notice.getChat();
    const msgs = await withTimeout(chat.fetchMessages({ limit: 5 }), CALL_TIMEOUT_MS, 'fetchMessages');
    const m = msgs.find((x) => x.id._serialized === notice.id._serialized);
    return Boolean(m && m.ack >= 3);
  } catch { return false; }
}

async function onCustomer(msg, text) {
  const chatId = msg.from;
  const c = state.chat(chatId);
  if (!c.phone) c.phone = await realPhone(msg);
  const phone = c.phone;
  state.remember(chatId, 'user', text);
  c.lastIn = msg.id?._serialized || '';

  if (!cfg?.enabled) return;
  if (cfg.shadow) return onCustomerShadow(msg, chatId, c, phone, text);
  if (botLabel) {
    // מנוהל לפי הרשימה "בוט": עונים רק שם; לקוח חדש — מצטרף אליה אוטומטית
    if (!state.data.botActive) return;                               // עצרת לכולם — השיחות אצלך
    let inList = await inBotList(chatId);
    if (!inList && !c.known) {
      c.known = true;
      state.save();
      if (cfg.auto_new !== false && await looksNew(msg)) inList = await setBotList(chatId, true);
    }
    c.known = true;
    if (!inList) return;                                              // שיחה שלך
  } else {
    if (!state.data.botActive || c.handedOff) {
      await toAdmin(`💬 *הודעה מ-${phone}*${c.handedOff ? ' (ממתין לנציג)' : ' (הבוט עצור)'}\n💭 ${text}\n🔗 https://wa.me/${phone}`);
      return;
    }
    if (state.paused(chatId)) return;                                 // אתה בשיחה
  }

  const open = inHours();
  let adminRead = false;
  const adminActive = Date.now() - adminLastSeen < ADMIN_ACTIVE_MS;
  if (open) {
    const notice = await toAdmin(`💬 *הודעה חדשה* · ${phone}\n💭 ${text}\n🔗 https://wa.me/${phone}` +
      (adminActive ? '\n_הבוט יענה בעוד 5 שניות — אם לא תענה_' : ''));
    if (!adminActive) adminRead = await adminReadSoon(notice);
  }
  schedule(msg, chatId, phone, text, open, replyDelayMs({ inHours: open, adminActive, adminRead }));
}

function schedule(msg, chatId, phone, text, open, delay) {
  clearTimeout(pending.get(chatId));
  pending.set(chatId, setTimeout(() => {
    pending.delete(chatId);
    replyNow(msg, chatId, phone, text, open).catch((e) => console.error('reply:', e.message));
  }, delay));
}

// ------------------------------------------------------------------ מצב השוואה (shadow)
// הבוט לא שולח כלום ללקוח ולא נוגע ברשימות. לכל הודעת לקוח: שואל את ה-AI מה היה עונה, ורושם בדשבורד
// ליד מה שנשלח בפועל מהמספר (הבוט הקיים / את/ה) — להשוואה, לפני שעוברים.

const THEIRS_WINDOW_MS = 30 * 60e3;   // תשובה "שלהם" נצמדת לשאלה האחרונה בשיחה, עד חצי שעה אחריה

async function onCustomerShadow(msg, chatId, c, phone, text) {
  const key = msg.id?._serialized;
  if (!key) return;
  // רק לקוחות: שיחה ברשימה "בוט" או שיחה חדשה (לא משפחה/חברים — כמו במצב הרגיל). ההחלטה פעם אחת לשיחה.
  if (c.shadowOk === undefined) {
    c.shadowOk = Boolean((botLabel && await withTimeout(inBotList(chatId), CALL_TIMEOUT_MS, 'labels').catch(() => false)) ||
      await looksNew(msg));
  }
  if (!c.shadowOk) return;
  c.lastQ = key;
  c.lastQAt = Date.now();
  state.save();
  srv.compare({ key, phone, question: text }).catch((e) => console.warn('compare:', e.message));
  if (!state.allowReply(chatId)) return;                              // לא יותר מדי קריאות AI ברצף
  setTimeout(() => shadowAnswer(msg, chatId, phone, text, key).catch((e) => console.error('shadow:', e.message)), 800);
}

async function shadowAnswer(msg, chatId, phone, text, key) {
  let crm = null;
  try { crm = await srv.customer(phone); } catch { /* לא לקוח */ }
  const open = inHours();
  const history = state.chat(chatId).history.slice(0, -1);
  const res = await ai.answer(cfg, { botName: cfg.bot_name, businessInfo: cfg.business_info, inHours: open,
    hoursText: cfg.hours, crm, history, text });
  if (res.error) { stats.ai_errors += 1; status.error = `AI: ${res.error}`; }
  else if (status.error?.startsWith('AI:')) status.error = '';
  status.ai = res.ai;
  let out = res.text || '';
  if (res.tags.some((t) => t.kind === 'ADDRESS')) {
    const block = ai.addressBlock(cfg.business_info);
    if (block) out = `${out ? out + '\n\n' : ''}${block}`;
  }
  // הפעולות שהבוט היה עושה — כתובות בסוף, כדי לראות גם אותן בהשוואה
  const acts = res.tags.map((t) => t.kind === 'HUMAN' ? '[מעביר לנציג]' : t.kind === 'BLOCK' ? `[חוסם: ${t.arg}]` :
    t.kind === 'ESCALATE' ? `[לאישורך: ${t.arg}]` : '').filter(Boolean);
  if (acts.length) out = `${out}\n${acts.join(' ')}`.trim();
  stats.compared = (stats.compared || 0) + 1;
  await srv.compare({ key, ours: out || '(אין תשובה)' });
}

/** הודעה שנשלחה מהמספר בשיחה עם לקוח (לא ע"י הבוט שלנו) — "התשובה שלהם" לשאלה האחרונה. */
async function onTheirReply(chatId, body) {
  const c = state.data.chats[chatId];
  if (!c?.lastQ || Date.now() - (c.lastQAt || 0) > THEIRS_WINDOW_MS) return;
  state.remember(chatId, 'assistant', body);                         // ההיסטוריה = מה שנאמר באמת
  await srv.compare({ key: c.lastQ, theirs: body }).catch((e) => console.warn('compare:', e.message));
}

/** הבוט מעביר אליך את השיחה: מוציא אותה מהרשימה "בוט" (או מסמן "ממתין לנציג"). */
async function handOff(chatId) {
  stats.handoffs += 1;
  if (botLabel) return setBotList(chatId, false);
  state.chat(chatId).handedOff = true;
  state.save();
  return true;
}

async function replyNow(msg, chatId, phone, text, open) {
  if (cfg?.shadow) return;                                            // מצב השוואה — לא שולחים ללקוחות
  if (botLabel ? !(await inBotList(chatId)) : state.paused(chatId)) return;   // ענית בינתיים / הוצאת מהרשימה
  state.chat(chatId).answered = msg.id?._serialized || '';
  if (!state.allowReply(chatId)) {                                    // הרבה מדי ברצף — מעבירים אליך
    if (botLabel || !state.chat(chatId).handedOff) {
      await handOff(chatId);
      await toAdmin(`⚠️ שיחה ארוכה עם ${phone} — הבוט עצר ומעביר אליך.\n🔗 https://wa.me/${phone}`);
    }
    return;
  }
  let crm = null;
  try { crm = await srv.customer(phone); } catch { /* לא לקוח — עדיין עונים */ }
  const history = state.chat(chatId).history.slice(0, -1);
  const res = await ai.answer(cfg, { botName: cfg.bot_name, businessInfo: cfg.business_info, inHours: open,
    hoursText: cfg.hours, crm, history, text });
  if (res.error) { stats.ai_errors += 1; console.warn('AI:', res.error); status.error = `AI: ${res.error}`; }
  else if (status.error?.startsWith('AI:')) status.error = '';
  status.ai = res.ai;
  let out = res.text;
  if (res.tags.some((t) => t.kind === 'ADDRESS')) {
    const block = ai.addressBlock(cfg.business_info);
    if (block) out = `${out ? out + '\n\n' : ''}${block}`;
  }
  if (out) {
    // סימון שהבוט ענה (כמו הסימון של וואטסאפ) — אם הופעל בדשבורד
    const tagged = cfg.mark_ai !== false && cfg.ai_tag ? `${out}\n\n${cfg.ai_tag}` : out;
    await send(chatId, tagged, { typing: true });
    state.remember(chatId, 'assistant', out);
    stats.replies += 1;
  }
  const deviceId = crm?.devices?.length === 1 ? crm.devices[0].device_id : null;
  for (const t of res.tags) {
    if (t.kind === 'HUMAN') {
      await handOff(chatId);
      const sum = state.chat(chatId).history.slice(-6).map((h) => `${h.role === 'user' ? 'לקוח' : 'בוט'}: ${h.content}`).join('\n');
      const back = botLabel ? `בשיחה: הכפתור למעלה → "${botLabel.name}"` : '"בוט" → 5';
      await toAdmin(`🙋 *לקוח מבקש נציג* · ${phone}\n🔗 https://wa.me/${phone}\n\n${sum}\n\n_להחזרת הבוט ללקוח: ${back}_`);
    } else if (t.kind === 'BLOCK' && t.arg && deviceId) {
      await srv.block(deviceId, [t.arg]).catch((e) => console.warn('block:', e.message));
    } else if (t.kind === 'ESCALATE' && deviceId) {
      await srv.escalate(deviceId, t.arg || text).catch((e) => console.warn('escalate:', e.message));
      await toAdmin(`🔐 *בקשה לאישורך* · ${phone} · מכשיר #${deviceId}\n${t.arg || text}\n(בדשבורד → בקשות)`);
    }
  }
}

// ------------------------------------------------------------------ הפעלה

process.on('unhandledRejection', (e) => console.error('unhandled:', e?.message || e));
for (const sig of ['SIGTERM', 'SIGINT']) {
  process.on(sig, async () => {
    state.flush();
    if (client) { try { await client.destroy(); } catch { /* */ } }
    process.exit(0);
  });
}

(async () => {
  console.log(`FilterPhone WhatsApp bot ${VERSION} → ${API}`);
  await loadConfig();
  setInterval(loadConfig, CONFIG_EVERY_MS);
  setTimeout(pollOutbox, OUTBOX_EVERY_MS);
  setTimeout(catchUp, CATCHUP_EVERY_MS);
})();
