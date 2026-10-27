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
 *   • מצב לימוד: עונים ללקוח בעצמך — הבוט זוכר את התשובה לשאלה שלו (ויודע לענות בפעם הבאה). לא יודע מה
 *     לענות — אומר ללקוח שהוא בודק ושואל אותך בצ'אט עם עצמך; "השב" על השאלה — נשלח ללקוח ונשמר.
 *     ללמד ישירות: "למד: ..." / "ש: ...\nת: ...", "מה למדת", "שכח 12". הכול גם בדשבורד.
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
const { replyDelayMs, combineBurst, typingMs, parseHashReply, waNumber, isVoice } = require('./lib/logic');
const labels = require('./lib/labels');
const know = require('./lib/knowledge');
const aimark = require('./lib/aimark');
const acts = require('./lib/actions');
const pcode = require('./lib/parentcode');

const VERSION = '2.6.0';
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
const KNOWLEDGE_EVERY_MS = 5 * 60e3; // רענון מה שהבוט למד (שינויים מהדשבורד)
const QUESTION_JOIN_MS = 3 * 60e3;   // כמה הודעות רצופות של לקוח = שאלה אחת
const ANSWER_WINDOW_MS = 30 * 60e3;  // תשובה שלך עד חצי שעה אחרי השאלה — נלמדת
const ANSWER_SETTLE_MS = 90e3;       // מחכים שתסיים לכתוב (כמה הודעות = תשובה אחת) לפני שלומדים
const ASK_AGAIN_MS = 30 * 60e3;      // לא שואלים אותך שוב על אותו לקוח בתוך חצי שעה

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
const parentCodes = pcode.store();       // פעולות שממתינות לקוד הורים (לפי שיחה / מכשיר באפליקציה)
let cfg = null;               // ההגדרות מהדשבורד
let client = null;            // מחובר רק כשהבוט מופעל
let status = { state: 'off' };
let me = '';                  // המספר שלי (972…@c.us)
let adminLastSeen = 0;
let lastOutboxId = 0;
const botMsgIds = new Set();  // הודעות שהבוט שלח — כדי לא להתייחס אליהן כאילו אתה כתבת
const acks = new Map();       // מזהה הודעה → {ack, at} (האישורים האחרונים מוואטסאפ — לבדיקת סימון ה-AI)
const ackWaiters = new Map();
// אותו דבר לפי תוכן: וואטסאפ לפעמים מדווח על ההודעה לפני ש-sendMessage מחזיר את המזהה
const outgoing = new Map();   // טקסט → עד מתי לצפות לו
const appThreads = new Map(); // הודעת העברה בצ'אט שלך → {deviceId, question, phone} (לתשובה ב"השב")
// מה שהלקוח רואה באפליקציה כשהבוט לא ענה לבד ומעביר אליך (הודעת ביניים נעימה)
const APP_CHECKING = 'קיבלנו את פנייתך 🙏 בודקים ונחזור אליך כאן בהקדם.';
const pending = new Map();    // תשובות שמחכות (5 שניות כשאתה פעיל — שתספיק לענות בעצמך)
const bursts = new Map();     // הודעות של לקוח שעוד לא נענו (chatId → {parts, gen,...}) — הודעה חדשה בזמן
                              // שהבוט עוד כותב מבטלת את התשובה הקודמת, והתשובה החדשה עונה על הכול
const stats = { replies: 0, handoffs: 0, app_forwarded: 0, app_replies: 0, ai_errors: 0, learned: 0, asked: 0, voice: 0 };
// הודעות קוליות: מתומללות ב-AI שבדשבורד, והבוט עונה בכתב. ההיסטוריה שומרת את התמלול עם 🎤.
const VOICE_MARK = '🎤 ';
const VOICE_UNHEARD = '🎤 (הודעה קולית — התמלול לא הצליח)';
const VOICE_NOTE = '🎤 (הודעה קולית)';                         // שיחה שהבוט לא מטפל בה — לא מתמללים
const unvoice = (t) => (t.startsWith(VOICE_MARK) ? t.slice(VOICE_MARK.length) : t);
const heardVoice = (t) => t.startsWith(VOICE_MARK) && t !== VOICE_UNHEARD && t !== VOICE_NOTE;
const transcripts = new Map();  // מזהה הודעה → התמלול (שלא נתמלל פעמיים: הודעה + השלמה אחרי הפעלה מחדש)
const inbox = new Map();        // שיחה → התור שלה: הודעות הלקוח מטופלות לפי הסדר (תמלול לוקח כמה שניות)
let knowledge = [];           // מה שהבוט למד ממך (מהשרת)
let knowledgeAt = 0;
const drafts = new Map();     // שיחה → תשובה שלך שעוד נכתבת (ללמידה)
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
    console.log('מחובר ✓', me);
    status.state = 'ready';
    status.progress = null;
    refreshLabel().finally(() => report({ state: 'ready', qr: '', error: '' }));
  });
  c.on('disconnected', (r) => onDisconnected(c, r));
  c.on('message_create', (msg) => onMessage(msg).catch((e) => console.error('message:', e.message)));
  c.on('message_ack', (m, ack) => onAck(m?.id?._serialized, ack));
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
    if (client && status.state === 'ready' && botLabel && cfg?.enabled && state.data.botActive) {
      for (const chat of await withTimeout(client.getChatsByLabelId(botLabel.id), CALL_TIMEOUT_MS, 'getChatsByLabelId')) {
        const chatId = chat?.id?._serialized;
        if (!chatId || chat.isGroup || pending.has(chatId) || chatId === adminChat()) continue;
        const last = chat.lastMessage;
        const c = state.chat(chatId);
        if (inbox.has(chatId) || !labels.needsCatchUp(last, { answered: c.answered || '' })) continue;
        const text = isVoice(last) ? await voiceText(last, true) : last.body.trim();
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

/**
 * שליחה. ללקוח (לא לצ'אט שלך): בלי "נקרא" — הלקוח לא מקבל וי כחול, ואצלך השיחה נשארת "לא נקראה"
 * (אם הייתה כזו לפני התשובה) — כך כשתיכנס לוואטסאפ תראה מי כתב, והבוט רק עונה ברקע.
 */
async function send(to, text, { typing = false, guard = null, extra = null } = {}) {
  if (!client) return null;
  const toCustomer = to !== adminChat() && to !== me;
  let chat = null;
  let unread = false;
  if (typing || toCustomer) {
    try {
      chat = await client.getChatById(to);
      unread = toCustomer && chat.unreadCount !== 0;        // ‎-1 = סומן "לא נקרא" ידנית
    } catch { /* לא קריטי */ }
  }
  if (typing && chat) {
    try {
      await chat.sendStateTyping();
      await sleep(typingMs(text));
      await chat.clearState();
    } catch { /* לא קריטי */ }
  }
  if (guard && !guard()) return null;                    // בינתיים הגיעה הודעה חדשה — התשובה הזו כבר לא רלוונטית
  const key = String(text).trim();
  outgoing.set(key, Date.now() + 60e3);
  const opts = { ...(extra ? { extra } : {}), ...(toCustomer ? { sendSeen: false } : {}) };
  const m = await client.sendMessage(to, text, opts);
  if (m?.id?._serialized) botMsgIds.add(m.id._serialized);
  if (unread && chat) chat.markUnread().catch((e) => console.warn('markUnread:', e.message));
  return m;
}

function onAck(id, ack) {
  if (!id) return;
  const now = Date.now();
  acks.set(id, { ack, at: now });
  for (const [k, v] of acks) if (now - v.at > 120e3) acks.delete(k);
  ackWaiters.get(id)?.(ack);
}

/** האישור הראשון מוואטסאפ על ההודעה: 1+ = התקבלה בשרת, ‎-1 = נדחתה, null = לא הגיע בזמן. */
function waitAck(id, ms) {
  return new Promise((resolve) => {
    const done = (ack) => { clearTimeout(t); ackWaiters.delete(id); resolve(ack); };
    const t = setTimeout(() => done(null), ms);
    const seen = acks.get(id)?.ack;
    if (seen === -1 || seen >= 1) { done(seen); return; }
    ackWaiters.set(id, (ack) => { if (ack === -1 || ack >= 1) done(ack); });
  });
}

function aiMarkFailed(why) {
  if (state.data.aiMarkFailedAt && Date.now() - state.data.aiMarkFailedAt < aimark.RETRY_AFTER_MS) return;
  state.data.aiMarkFailedAt = Date.now();
  state.save();
  console.warn('סימון AI של וואטסאפ לא התקבל:', why);
  toAdmin('ℹ️ וואטסאפ לא קיבל את סימון ה-AI המקורי בחשבון הזה — בינתיים הבוט מסמן בטקסט (מהדשבורד). ננסה שוב בעוד שבוע.')
    .catch(() => {});
}

/** תשובה של הבוט ללקוח — עם הסימון ✦AI של וואטסאפ ליד השעה (או בטקסט, אם וואטסאפ לא מאפשר). */
async function sendAi(to, text, opts = {}) {
  if (!aimark.nativeOn(cfg, state.data.aiMarkFailedAt)) return send(to, aimark.withTextTag(cfg, text), opts);
  let m;
  try {
    m = await send(to, text, { ...opts, extra: { bizBotType: aimark.BIZ_BOT_TYPE } });
  } catch (e) {
    aiMarkFailed(e.message);
    return send(to, aimark.withTextTag(cfg, text), { ...opts, typing: false });
  }
  const id = m?.id?._serialized;
  if (id) {
    waitAck(id, 20e3).then((ack) => {
      if (ack !== -1) return;
      aiMarkFailed('ack -1');
      send(to, aimark.withTextTag(cfg, text)).catch((e) => console.warn('resend:', e.message));
    });
  }
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
          try { await handleAppSupport(m, waIds); }
          catch (e) { console.warn('app-support:', e.message); }
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

function rememberAppThread(sent, deviceId, question, phone) {
  if (sent?.id?._serialized) appThreads.set(sent.id._serialized, { deviceId, question, phone });
}

/**
 * מבצע את הפעולות שה-AI ביקש (חסימה / פתיחה זמנית / סגירה) במכשיר שזוהה — לפני שהתשובה נשלחת, כדי
 * שהלקוח יקבל אישור אמיתי (עם השעה) ולא הבטחה. השרת אוכף את הגבולות (מה מותר, עד שעה); מה שנדחה —
 * עובר לאישורך. מחזיר שורות אישור ללקוח, ומה בוצע (בשבילך).
 */
async function runActions(deviceId, crm, tags, parentCode = null) {
  const confirms = [];
  const done = [];
  const needCode = [];          // קוד הורים: הפעולות שהשרת דחה בלי קוד / עם קוד שגוי — ממתינות להקלדה
  const codeErrors = [];
  const wanted = tags.filter((t) => ['BLOCK', 'OPEN', 'CLOSE'].includes(t.kind) && t.arg);
  if (!wanted.length) return { confirms, done, needCode, locked: false };
  if (!deviceId) {
    confirms.push('כדי שאוכל לבצע את זה — שלחו לי את ההודעה מאפליקציית FilterPhone (טאב "תמיכה" ← "תמיכה בוואטסאפ"), שם מופיעים מספר המכשיר והמזהה.');
    return { confirms, done };
  }
  for (const t of wanted) {
    try {
      if (t.kind === 'BLOCK') {
        const r = await srv.block(deviceId, t.arg.split(',').map((x) => x.trim()).filter(Boolean), parentCode);
        confirms.push(acts.confirmLine('BLOCK', r));
        if (r.titles?.length) done.push(`🔒 חסם: ${r.titles.join(', ')}`);
      } else if (t.kind === 'OPEN') {
        const o = acts.parseOpen(t.arg, { max: crm?.actions?.max_minutes || 60, def: crm?.actions?.default_minutes || 10 });
        const r = await srv.open(deviceId, o.item, o.minutes, parentCode);
        confirms.push(acts.confirmLine('OPEN', r));
        if (!r.already_open) done.push(`🔓 פתח זמנית: ${r.title} ל-${r.minutes} דק' (עד ${acts.ilTime(r.until)})`);
      } else {
        const r = await srv.close(deviceId, t.arg.trim());
        const line = acts.confirmLine('CLOSE', r);
        if (line) { confirms.push(line); done.push(`🔒 סגר מוקדם: ${r.title}`); }
      }
    } catch (e) {
      if (pcode.isCodeError(e)) {                  // קוד הורים — לא "לאישור המנהל": הלקוח מקליד את הקוד
        needCode.push(t);
        codeErrors.push(e);
        continue;
      }
      console.warn(`action ${t.kind}:${t.arg}:`, e.message);
      confirms.push('⚠️ את זה אני לא יכול לבצע לבד — העברתי לאישור המנהל.');
      done.push(`⚠️ לא בוצע (${t.kind} ${t.arg}): ${e.message} — נשלח לאישורך`);
      await srv.escalate(deviceId, `${t.kind === 'OPEN' ? 'פתיחה' : t.kind === 'BLOCK' ? 'חסימה' : 'סגירה'}: ${t.arg}`)
        .catch(() => {});
    }
  }
  if (codeErrors.length) confirms.push(pcode.message(codeErrors));
  return { confirms: [...new Set(confirms)], done, needCode, locked: codeErrors.some((e) => e.locked) };
}

/**
 * קוד הורים: הלקוח הקליד את הקוד אחרי שהבוט ביקש — מבצעים את הפעולות שחיכו לו. הקוד לא נשמר
 * בהיסטוריה ולא עובר ל-AI. true = ההודעה טופלה כאן. הודעה שאינה קוד — מבטלת את ההמתנה.
 */
async function tryParentCode(key, deviceId, text, reply) {
  const p = parentCodes.get(key);
  if (!p) return false;
  const code = pcode.extract(text);
  if (!code) { parentCodes.take(key); return false; }
  const ran = await runActions(p.deviceId || deviceId, p.crm, p.tags, code);
  if (ran.needCode.length && !ran.locked) parentCodes.set(key, { ...p, tags: ran.needCode });
  else parentCodes.take(key);
  if (ran.done.length) {
    toAdmin(`🤖 *הבוט ביצע (עם קוד הורים)* · מכשיר #${p.deviceId}\n${ran.done.join('\n')}`).catch(() => {});
  }
  await reply(ran.confirms.join('\n\n') || '✓');
  return true;
}

/**
 * פנייה מצ'אט התמיכה באפליקציה: הבוט עונה ללקוח *בתוך האפליקציה* ב-AI (אותו מוח של וואטסאפ + מה שלמד).
 *   • בטוח בתשובה → עונה ללקוח מיד באפליקציה, ואצלך בצ'אט מופיע מה ענה (אפשר לתקן/להוסיף ב"השב").
 *   • לא בטוח / אין מפתח AI → הלקוח מקבל "בודקים ונחזור", ואתה מקבל את השאלה; תשובתך חוזרת ללקוח ונשמרת.
 *   • יש לבוט את מזהה המכשיר, לכן הוא חוסם / פותח זמנית בעצמו, ומעביר פעולה רגישה לאישורך (ESCALATE).
 */
async function handleAppSupport(m, waIds) {
  const deviceId = m.device_id;
  const phone = String(m.phone || '').replace(/\D/g, '');
  // הצעת ייעול (הגדרות באפליקציה): ישר אליך, בלי מענה AI — והלקוח מקבל "תודה"
  if (m.topic === 'suggestion') {
    const text = String(m.text || '').replace(/^💡\s*הצעת ייעול:\s*/, '');
    const sent = await toAdmin(`💡 *הצעת ייעול* · ${m.customer || 'לקוח'}${m.phone ? ' · ' + m.phone : ''} · מכשיר #${deviceId}\n\n${text}\n\n↩️ "השב" כאן — והתשובה תגיע ללקוח באפליקציה`);
    rememberAppThread(sent, deviceId, m.text, phone);
    try { await srv.reply(deviceId, 'תודה! ההצעה הועברה למנהל 🙏'); } catch { /* */ }
    stats.app_suggestions = (stats.app_suggestions || 0) + 1;
    return;
  }
  // דוח אבחון (הגדרות באפליקציה): ישר אליך עם המצב הטכני, בלי מענה AI
  if (m.topic === 'diag') {
    const d = m.diag || {};
    const lines = ['vpn', 'vpn_error', 'accessibility', 'device_owner', 'battery_unrestricted', 'ca_installed',
      'paused', 'policy_version', 'level', 'app_version', 'sdk']
      .filter((k) => d[k] !== undefined && d[k] !== '')
      .map((k) => `• ${k}: ${d[k]}`).join('\n');
    const sent = await toAdmin(`📋 *דוח אבחון* · ${m.customer || 'לקוח'}${m.phone ? ' · ' + m.phone : ''} · מכשיר #${deviceId}\n${lines || '(אין נתונים)'}\n\n↩️ "השב" כאן — והתשובה תגיע ללקוח באפליקציה`);
    rememberAppThread(sent, deviceId, m.text, phone);
    try { await srv.reply(deviceId, 'הדוח התקבל אצל המנהל ✓'); } catch { /* */ }
    return;
  }
  // קוד הורים שהבוט ביקש — מבוצע כאן (לא עובר ל-AI ולא מועבר אליך)
  if (await tryParentCode(`app:${deviceId}`, deviceId, m.text, (out) => srv.reply(deviceId, out))) return;
  const head = `📩 *פנייה מהאפליקציה* · ${m.customer || 'לקוח'}${m.phone ? ' · ' + m.phone : ''} · מכשיר #${deviceId}`;
  let crm = null;
  if (phone) { try { crm = await srv.customer(phone, { device: deviceId }); } catch { /* לא לקוח — עדיין עונים */ } }

  const sel = know.select(await loadKnowledge(), m.text);
  const res = await ai.answer(cfg, { botName: cfg.bot_name, businessInfo: cfg.business_info, inHours: inHours(),
    hoursText: cfg.hours, crm, history: [], text: m.text, knowledge: know.promptSection(sel),
    askWhenUnsure: cfg.ask_when_unsure !== false, actions: acts.promptSection(crm) });
  if (sel.qas.length) srv.used(sel.qas.map((q) => q.id)).catch(() => {});
  if (res.error) { stats.ai_errors += 1; status.error = `AI: ${res.error}`; }
  else if (status.error?.startsWith('AI:')) status.error = '';
  status.ai = res.ai;

  // פעולות שהבוט מבצע בעצמו (יש לו את מזהה המכשיר): חסימה / פתיחה זמנית, והסלמה לאישורך
  const deviceOps = deviceId;                                          // מהאפליקציה — המכשיר ודאי
  const ran = await runActions(deviceOps, crm, res.tags);
  if (ran.needCode.length && !ran.locked) parentCodes.set(`app:${deviceId}`, { deviceId: deviceOps, crm, tags: ran.needCode });
  if (ran.done.length) await toAdmin(`🤖 *הבוט ביצע* · מכשיר #${deviceOps}${m.phone ? ' · ' + m.phone : ''}\n${ran.done.join('\n')}`);
  for (const t of res.tags) {
    if (t.kind === 'ESCALATE') {
      await srv.escalate(deviceOps, t.arg || m.text).catch((e) => console.warn('escalate:', e.message));
      await toAdmin(`🔐 *בקשה לאישורך* · מכשיר #${deviceOps}${m.phone ? ' · ' + m.phone : ''}\n${t.arg || m.text}\n(בדשבורד → בקשות)`);
    }
  }

  const unsure = !res.text || res.ai === 'none' || res.tags.some((t) => t.kind === 'ASK' || t.kind === 'HUMAN');
  if (!unsure) {
    let out = [res.text, ...ran.confirms].filter(Boolean).join('\n\n');
    if (res.tags.some((t) => t.kind === 'ADDRESS')) {
      const block = ai.addressBlock(cfg.business_info);
      if (block) out = `${out ? out + '\n\n' : ''}${block}`;
    }
    try { await srv.reply(deviceId, out); stats.app_replies += 1; }
    catch (e) { await toAdmin(`⚠️ התשובה למכשיר #${deviceId} לא נשלחה: ${e.message}`); }
    const sent = await toAdmin(`${head}\n\n${m.text}\n\n🤖 *הבוט ענה ללקוח:*\n${out}\n\n↩️ לתיקון/תוספת: "השב" או #${deviceId} <טקסט>`);
    rememberAppThread(sent, deviceId, m.text, phone);
  } else {
    try { await srv.reply(deviceId, APP_CHECKING); } catch { /* */ }
    const q = res.tags.find((t) => t.kind === 'ASK')?.arg;
    const why = res.error ? 'ה-AI לא היה זמין לרגע'
      : res.ai === 'none' ? 'אין מפתח AI מוגדר בדשבורד' : (q || 'צריך את העזרה שלך');
    const sent = await toAdmin(`${head}\n\n${m.text}\n\n🤔 *הבוט לא ענה לבד* (${why}).\n"השב" כאן והתשובה תחזור ללקוח, או: #${deviceId} <תשובה>`);
    rememberAppThread(sent, deviceId, m.text, phone);
  }
}

// ------------------------------------------------------------------ הודעות

async function onMessage(msg) {
  if (msg.from?.endsWith('@g.us') || msg.to?.endsWith('@g.us')) return;   // קבוצות
  if (msg.from === 'status@broadcast' || msg.isStatus) return;
  const body = (msg.body || '').trim();
  const voice = isVoice(msg);
  if (!body && !voice) return;
  if (msg.fromMe) {
    const selfChat = msg.to === me || msg.to === msg.from;
    if (voice) return selfChat ? undefined : onAdminVoice(msg);           // הודעה קולית לעצמך — לא פקודה
    if (sentByBot(msg.id?._serialized, body)) return;                     // הבוט עצמו — לא "אתה כתבת"
    return onAdmin(msg, body, selfChat);
  }
  if (adminChat() !== me && (msg.from === adminChat() || (await realPhone(msg)) === adminChat().replace(/@.+/, ''))) {
    return voice ? undefined : onAdmin(msg, body, true);                 // אתה, ממספר המנהל
  }
  return inOrder(msg.from, () => onCustomer(msg, body, voice));
}

/** הודעות של אותה שיחה — אחת אחרי השנייה (טקסט שנשלח אחרי הודעה קולית לא "עוקף" את התמלול שלה). */
function inOrder(chatId, fn) {
  const run = (inbox.get(chatId) || Promise.resolve()).then(fn);
  const tail = run.catch(() => {});
  inbox.set(chatId, tail);
  tail.then(() => { if (inbox.get(chatId) === tail) inbox.delete(chatId); });
  return run;
}

/** תמלול הודעה קולית (פעם אחת לכל הודעה). '' — אין AI / אין דיבור / תקלה. */
async function transcript(msg) {
  const id = msg.id?._serialized || '';
  if (id && transcripts.has(id)) return transcripts.get(id);
  let text = '';
  try {
    const media = await withTimeout(msg.downloadMedia(), CALL_TIMEOUT_MS, 'downloadMedia');
    text = await ai.transcribe(cfg, media);
    if (text) stats.voice += 1;
  } catch (e) {
    console.warn('voice:', e.message);
  }
  if (id) {
    transcripts.set(id, text);
    if (transcripts.size > 200) transcripts.delete(transcripts.keys().next().value);
  }
  return text;
}

/** הטקסט של הודעה קולית: התמלול (רק כשהבוט מטפל בשיחה — לא שולחים ל-AI הקלטות פרטיות), או סימון. */
async function voiceText(msg, handled) {
  if (!handled) return VOICE_NOTE;
  if (!cfg?.ai_key) return VOICE_UNHEARD;
  const t = await transcript(msg);
  return t ? VOICE_MARK + t : VOICE_UNHEARD;
}

/** שלחת ללקוח הודעה קולית — הבוט זז הצידה מיד, ובהיסטוריה (וללמידה) נשמר התמלול. */
async function onAdminVoice(msg) {
  const chatId = msg.to;
  cancelPending(chatId);                                   // לפני התמלול — שהבוט לא יענה בינתיים
  const c = state.chat(chatId);
  const text = await voiceText(msg, Boolean(cfg?.enabled && c.customer));
  return onAdmin(msg, text, false, heardVoice(text));
}

/**
 * אתה כתבת. בצ'אט עם עצמך — תפריט / תשובה ללקוח באפליקציה. בשיחה עם לקוח — הבוט זז הצידה:
 * מוציא את השיחה מהרשימה "בוט" (או, בלי רשימה, שותק בה לזמן שנקבע).
 */
async function onAdmin(msg, body, selfChat, learnable = true) {
  adminLastSeen = Date.now();
  if (!selfChat) {
    const chatId = msg.to;
    cancelPending(chatId);
    const c = state.chat(chatId);
    c.known = true;
    state.remember(chatId, 'assistant', body);
    state.closeAsks(chatId);                                         // ענית לו בעצמך
    if (learnable) noteOwnerReply(chatId, c, unvoice(body));         // מצב לימוד
    if (botLabel) await setBotList(chatId, false);
    else state.pause(chatId, (cfg?.handoff_minutes || 60) * 60e3);
    return;
  }
  const quoted = msg.hasQuotedMsg ? await msg.getQuotedMessage().catch(() => null) : null;
  const quotedId = quoted?.id?._serialized || '';
  // "השב" על שאלה שהבוט שאל אותך → התשובה ללקוח, ונשמרת לפעם הבאה
  const ask = quotedId && state.getAsk(quotedId);
  if (ask) return answerAsk(msg, ask, body);
  // תשובה ללקוח באפליקציה: "#123 טקסט", או "השב" על הודעת פנייה
  let reply = parseHashReply(body);
  if (!reply && quotedId && appThreads.has(quotedId)) {
    const t = appThreads.get(quotedId);
    reply = { deviceId: t.deviceId, text: body, thread: t };
  }
  if (reply) {
    try {
      await srv.reply(reply.deviceId, reply.text);
      stats.app_replies += 1;
      // לימוד: תשובתך לפנייה מהאפליקציה נשמרת, כדי שהבוט יֵדע לבד בפעם הבאה
      if (reply.thread?.question) {
        srv.learn({ kind: 'qa', question: reply.thread.question, answer: reply.text,
          phone: reply.thread.phone || '', source: 'app' })
          .then(() => { stats.learned += 1; loadKnowledge(true); }).catch(() => {});
      }
      await msg.react('✅').catch(() => {});
    } catch (e) {
      await toAdmin(`⚠️ התשובה למכשיר #${reply.deviceId} לא נשלחה: ${e.message}`);
    }
    return;
  }
  // פקודות לימוד: "למד: ...", "ש: ...\nת: ...", "שכח 12", "מה למדת"
  const cmd = know.parseCommand(body);
  if (cmd) return onTeach(msg, cmd);
  await onMenu(body);
}

// ------------------------------------------------------------------ מצב לימוד

/** מה שהבוט למד (מהשרת) — מתרענן כל כמה דקות, ומיד אחרי שלמד משהו חדש. */
async function loadKnowledge(force = false) {
  if (!force && Date.now() - knowledgeAt < KNOWLEDGE_EVERY_MS) return knowledge;
  try {
    knowledge = (await srv.knowledge()).items || [];
    knowledgeAt = Date.now();
  } catch (e) {
    console.warn('knowledge:', e.message);
  }
  return knowledge;
}

/** הלקוח כתב — זו "השאלה" שהתשובה הבאה שלך תיצמד אליה (כמה הודעות רצופות = שאלה אחת). */
function noteCustomerQuestion(chatId, c, text, learnable = true) {
  if (drafts.has(chatId)) flushDraft(chatId).catch((e) => console.warn('learn:', e.message));   // הלקוח כבר ענה לך
  const now = Date.now();
  if (!learnable) { c.q = { text, at: now, taken: true }; return; }     // הודעה קולית בלי תמלול — לא לומדים עליה
  const join = c.q && !c.q.taken && now - c.q.at < QUESTION_JOIN_MS;
  c.q = { text: (join ? `${c.q.text}\n${text}` : text).slice(-800), at: now };
}

/** ענית ללקוח בשיחה — אוספים את התשובה (אולי בכמה הודעות), ואחרי שסיימת לומדים אותה. */
function noteOwnerReply(chatId, c, body) {
  if (cfg?.learn_from_replies === false || !c.customer) return;      // רק שיחות עם לקוחות, לא משפחה/חברים
  let d = drafts.get(chatId);
  if (!d) {
    if (!c.q || c.q.taken || Date.now() - c.q.at > ANSWER_WINDOW_MS) return;
    c.q.taken = true;
    d = { question: c.q.text, phone: c.phone || '', parts: [], timer: null };
    drafts.set(chatId, d);
  }
  d.parts.push(body);
  clearTimeout(d.timer);
  d.timer = setTimeout(() => flushDraft(chatId).catch((e) => console.warn('learn:', e.message)), ANSWER_SETTLE_MS);
}

async function flushDraft(chatId) {
  const d = drafts.get(chatId);
  if (!d) return;
  drafts.delete(chatId);
  clearTimeout(d.timer);
  const answer = d.parts.join('\n').trim();
  if (!know.worthLearning(answer, d.question)) return;
  const item = await srv.learn({ kind: 'qa', question: d.question, answer, phone: d.phone, source: 'reply' });
  stats.learned += 1;
  knowledgeAt = 0;
  console.log(`למד מהתשובה שלך (#${item.id})`);
  await toAdmin(`📚 *למדתי מהתשובה שלך* (#${item.id})\nש: ${know.short(d.question)}\nת: ${know.short(answer)}\n_לא מדויק? "שכח ${item.id}"_`);
}

/** הבוט לא ידע לענות — שואל אותך בצ'אט עם עצמך. "השב" על השאלה → answerAsk. */
async function askOwner(chatId, phone, question, missing) {
  if (state.openAsk(chatId, ASK_AGAIN_MS)) return;                    // כבר שאל עליו — מחכה לך
  stats.asked += 1;
  const sent = await toAdmin(`❓ *לא ידעתי מה לענות* · ${phone}\n💭 הלקוח כתב: ${know.short(question, 400)}\n` +
    `🔎 חסר לי: ${missing || 'מידע'}\n\n↩️ *"השב" על ההודעה הזו* עם התשובה — אשלח ללקוח ואזכור לפעם הבאה.\n🔗 https://wa.me/${phone}`);
  const id = sent?.id?._serialized;
  if (id) state.addAsk(id, { chatId, phone, question, missing: missing || '' });
}

async function answerAsk(msg, ask, ownerText) {
  ask.answered = true;
  state.save();
  const c = state.chat(ask.chatId);
  // הבוט מנסח ללקוח הודעת המשך לפי התשובה שלך; בלי AI / בתקלה — התשובה שלך כמו שהיא
  let out = ownerText;
  let byAi = false;
  if (cfg?.ai_key && cfg.ai_provider !== 'none') {
    const extra = `=== בעל העסק ענה עכשיו על מה שבדקת ===\nהלקוח שאל: ${ask.question}\n${ask.missing ? `מה שחסר לך: ${ask.missing}\n` : ''}התשובה של בעל העסק: ${ownerText}\n=== סוף ===`;
    const res = await ai.answer(cfg, { botName: cfg.bot_name, businessInfo: cfg.business_info, inHours: inHours(),
      hoursText: cfg.hours, crm: null, knowledge: extra, askWhenUnsure: false, history: c.history.slice(-12),
      text: '[הודעה פנימית — לא מהלקוח] כתוב/כתבי עכשיו ללקוח הודעת המשך קצרה ("בדקתי, ...") עם התשובה של בעל העסק. בלי תגיות.' });
    if (!res.error && res.ai !== 'none' && res.text) { out = res.text; byAi = true; }
  }
  try {
    await (byAi ? sendAi(ask.chatId, out, { typing: true }) : send(ask.chatId, out, { typing: true }));
    state.remember(ask.chatId, 'assistant', out);
    stats.replies += 1;
  } catch (e) {
    return toAdmin(`⚠️ לא הצלחתי לשלוח ל-${ask.phone}: ${e.message}`);
  }
  let learnedNote = '';
  if (!know.hasPhone(ownerText)) {
    try {
      const item = await srv.learn({ kind: 'qa', question: ask.question, answer: ownerText, phone: ask.phone, source: 'ask' });
      stats.learned += 1;
      knowledgeAt = 0;
      learnedNote = `\n📚 נשמר לפעם הבאה (#${item.id}) — "שכח ${item.id}" לביטול`;
    } catch (e) {
      learnedNote = `\n⚠️ לא נשמר לפעם הבאה: ${e.message}`;
    }
  }
  await msg.react('✅').catch(() => {});
  return toAdmin(`✅ נשלח ל-${ask.phone}${byAi ? ' (בניסוח של הבוט)' : ''}:\n${know.short(out, 300)}${learnedNote}`);
}

/** פקודות לימוד בצ'אט עם עצמך. */
async function onTeach(msg, cmd) {
  try {
    if (cmd.cmd === 'list') return toAdmin(know.listText(await loadKnowledge(true)));
    if (cmd.cmd === 'forget') {
      await srv.forget(cmd.id);
      knowledgeAt = 0;
      await msg.react('🗑️').catch(() => {});
      return toAdmin(`🗑️ שכחתי את #${cmd.id}.`);
    }
    const item = await srv.learn({ kind: cmd.kind, question: cmd.question || '', answer: cmd.answer, source: 'teach' });
    stats.learned += 1;
    knowledgeAt = 0;
    await msg.react('📚').catch(() => {});
    return toAdmin(`📚 למדתי (#${item.id}). "מה למדת" — הרשימה · "שכח ${item.id}" — ביטול.`);
  } catch (e) {
    return toAdmin(cmd.cmd === 'forget' && /לא נמצא|404/.test(e.message) ? `⚠️ אין פריט #${cmd.id}.` : `⚠️ לא הצלחתי: ${e.message}`);
  }
}

const MENU = `🛠️ *תפריט הבוט*

💡 בכל שיחה: הכפתור למעלה (רשימות) → *בוט* — מחזיר את הבוט לשיחה; כתבת בעצמך — הבוט זז הצידה.

*1* - 🔴 עצור את הבוט לכולם
*2* - 🟢 הפעל את הבוט לכולם
*3* - 📊 סטטוס ושיחות
*4* - ⏸️ עצור לקוח מסוים
*5* - ▶️ הפעל לקוח מסוים (גם אחרי "נציג")
*6* - 🔄 מחק היסטוריה של לקוח
*7* - 📚 מה למדתי
*8* - ✏️ איך ללמד אותי

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
    const learning = cfg?.learn_from_replies === false ? '📚 לימוד מהתשובות שלך: כבוי' :
      `📚 למדתי: ${(await loadKnowledge()).length} · שאלתי אותך: ${stats.asked}`;
    return toAdmin(`📊 *סטטוס*\n${state.data.botActive ? '🟢 פעיל' : '🔴 עצור'} · ${open ? '☀️ שעות פעילות' : '🌙 מחוץ לשעות'}\n${mode}\n` +
      `תשובות: ${stats.replies} · הועברו אליך: ${stats.handoffs}\n${learning}\n\n${lines.join('\n') || '_אין שיחות_'}`);
  }
  if (text === '7') { menu.open = false; return toAdmin(know.listText(await loadKnowledge(true))); }
  if (text === '8') { menu.open = false; return toAdmin(know.TEACH_HELP); }
  const steps = { 4: 'pause', 5: 'resume', 6: 'reset' };
  if (steps[text]) { menu.step = steps[text]; return toAdmin('📞 שלח את מספר הטלפון של הלקוח (למשל 0501234567), או *0*.'); }
  return toAdmin('❓ שלח מספר 1–8, או *0*.');
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

/**
 * מי מטפל בהודעת הלקוח: 'bot' — הבוט עונה; 'notify' — רק הודעה אליך (בלי רשימה: הבוט עצור / ממתין
 * לנציג); 'quiet' — שיחה שלך / הבוט עצור / אתה באמצע שיחה. לקוח חדש מצטרף כאן לרשימה "בוט".
 */
async function customerRoute(msg, chatId, c) {
  if (botLabel) {
    // מנוהל לפי הרשימה "בוט": עונים רק שם; לקוח חדש — מצטרף אליה אוטומטית
    let inList = await inBotList(chatId);
    if (!inList && !c.known) {
      c.known = true;
      state.save();
      if (await looksNew(msg)) {
        c.customer = true;
        if (state.data.botActive && cfg.auto_new !== false) inList = await setBotList(chatId, true);
      }
    }
    c.known = true;
    if (inList) c.customer = true;                                    // לקוח — לומדים מהתשובות שלך אליו
    state.save();
    return state.data.botActive && inList ? 'bot' : 'quiet';          // עצרת לכולם / שיחה שלך
  }
  c.customer = true;                                                  // בלי רשימה — הבוט עונה לכולם
  if (!state.data.botActive || c.handedOff) return 'notify';
  return state.paused(chatId) ? 'quiet' : 'bot';                      // אתה בשיחה
}

async function onCustomer(msg, text, voice = false) {
  const chatId = msg.from;
  const c = state.chat(chatId);
  if (!c.phone) c.phone = await realPhone(msg);
  const phone = c.phone;
  const route = cfg?.enabled ? await customerRoute(msg, chatId, c) : 'off';
  if (voice) text = await voiceText(msg, route === 'bot');
  const learnable = !voice || heardVoice(text);
  // קוד הורים שהבוט ביקש — מבוצע כאן, לא נשמר בהיסטוריה ולא נשלח ל-AI
  if (cfg?.enabled && learnable && await tryParentCode(chatId, null, unvoice(text), (out) => sendAi(chatId, out, { typing: true }))) return;
  state.remember(chatId, 'user', text);
  c.lastIn = msg.id?._serialized || '';
  c.deviceHint = acts.deviceHint(unvoice(text), c.deviceHint || {});  // "מכשיר #16" + מזהה — מההודעה של האפליקציה
  noteCustomerQuestion(chatId, c, unvoice(text), learnable);

  if (route === 'notify') {
    await toAdmin(`💬 *הודעה מ-${phone}*${c.handedOff ? ' (ממתין לנציג)' : ' (הבוט עצור)'}\n💭 ${text}\n🔗 https://wa.me/${phone}`);
    return;
  }
  if (route !== 'bot') return;
  // ההמשך (התראה אליך, "קראת?", תזמון התשובה) — לא מעכב את ההודעה הבאה של הלקוח בתור
  notifyAndSchedule(msg, chatId, phone, text).catch((e) => console.error('message:', e.message));
}

async function notifyAndSchedule(msg, chatId, phone, text) {
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

/**
 * עונים מיד. הלקוח שלח עוד הודעה בזמן שהבוט עוד חושב / "מקליד"? התשובה הקודמת לא נשלחת, והחדשה
 * עונה על כל ההודעות יחד — כך הבוט לא עונה על חצי שאלה וגם לא שולח שתי תשובות כפולות.
 */
function schedule(msg, chatId, phone, text, open, delay) {
  const b = bursts.get(chatId) || { parts: [], gen: 0 };
  b.parts.push(text);
  b.msg = msg;                                   // ההודעה האחרונה — כדי לסמן/להגיב עליה
  b.phone = phone;
  b.open = open;
  b.gen += 1;
  bursts.set(chatId, b);
  const gen = b.gen;
  clearTimeout(pending.get(chatId));
  pending.set(chatId, setTimeout(() => {
    pending.delete(chatId);
    const n = b.parts.length;
    const current = () => bursts.get(chatId) === b && b.gen === gen;
    replyNow(b.msg, chatId, b.phone, combineBurst(b.parts), b.open, current)
      .then(() => {
        if (bursts.get(chatId) !== b || b.gen !== gen) return;   // הגיעה הודעה חדשה — התשובה הבאה עונה על הכול
        b.parts.splice(0, n);
        if (!b.parts.length) bursts.delete(chatId);
      })
      .catch((e) => console.error('reply:', e.message));
  }, delay));
}

/** מבטל תשובה שמחכה או שנכתבת עכשיו (ענית בעצמך / הלקוח הועבר לנציג). */
function cancelPending(chatId) {
  clearTimeout(pending.get(chatId));
  pending.delete(chatId);
  bursts.delete(chatId);
}

/** הבוט מעביר אליך את השיחה: מוציא אותה מהרשימה "בוט" (או מסמן "ממתין לנציג"). */
async function handOff(chatId) {
  stats.handoffs += 1;
  if (botLabel) return setBotList(chatId, false);
  state.chat(chatId).handedOff = true;
  state.save();
  return true;
}

async function replyNow(msg, chatId, phone, text, open, current = () => true) {
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
  try { crm = await srv.customer(phone, state.chat(chatId).deviceHint || {}); } catch { /* לא לקוח — עדיין עונים */ }
  const history = state.chat(chatId).history.slice(0, -1);
  // מה שלמד ממך: העובדות + שאלות דומות שכבר ענית עליהן (הודעה קצרה — יחד עם ההודעה הקודמת של הלקוח)
  const prevUser = [...history].reverse().find((h) => h.role === 'user')?.content || '';
  const sel = know.select(await loadKnowledge(), text.length < 25 && prevUser ? `${prevUser}\n${text}` : text);
  const res = await ai.answer(cfg, { botName: cfg.bot_name, businessInfo: cfg.business_info, inHours: open,
    hoursText: cfg.hours, crm, history, text, knowledge: know.promptSection(sel),
    askWhenUnsure: cfg.ask_when_unsure !== false, actions: acts.promptSection(crm) });
  if (sel.qas.length) srv.used(sel.qas.map((q) => q.id)).catch(() => {});
  if (!current()) return;
  // פעולות במכשיר (חסימה / פתיחה זמנית) — מבוצעות לפני השליחה, והלקוח מקבל אישור אמיתי עם השעה
  const deviceId = crm?.selected || null;
  const ran = await runActions(deviceId, crm, res.tags);
  if (ran.needCode.length && !ran.locked) parentCodes.set(chatId, { deviceId, crm, tags: ran.needCode });
  if (ran.done.length) {
    toAdmin(`🤖 *הבוט ביצע* · ${phone} · מכשיר #${deviceId}\n${ran.done.join('\n')}\n🔗 https://wa.me/${phone}`).catch(() => {});
  }                                             // הגיעה הודעה חדשה — עונים עליה (ועל זו) יחד
  if (res.error) { stats.ai_errors += 1; console.warn('AI:', res.error); status.error = `AI: ${res.error}`; }
  else if (status.error?.startsWith('AI:')) status.error = '';
  status.ai = res.ai;
  let out = [res.text, ...ran.confirms].filter(Boolean).join('\n\n');
  if (res.tags.some((t) => t.kind === 'ADDRESS')) {
    const block = ai.addressBlock(cfg.business_info);
    if (block) out = `${out ? out + '\n\n' : ''}${block}`;
  }
  if (out) {
    // עם הסימון ✦AI של וואטסאפ (או בטקסט, אם וואטסאפ לא מאפשר) — אם הופעל בדשבורד
    if (!(await sendAi(chatId, out, { typing: true, guard: current }))) return;
    state.remember(chatId, 'assistant', out);
    stats.replies += 1;
  }
  for (const t of res.tags) {
    if (t.kind === 'HUMAN') {
      await handOff(chatId);
      const sum = state.chat(chatId).history.slice(-6).map((h) => `${h.role === 'user' ? 'לקוח' : 'בוט'}: ${h.content}`).join('\n');
      const back = botLabel ? `בשיחה: הכפתור למעלה → "${botLabel.name}"` : '"בוט" → 5';
      await toAdmin(`🙋 *לקוח מבקש נציג* · ${phone}\n🔗 https://wa.me/${phone}\n\n${sum}\n\n_להחזרת הבוט ללקוח: ${back}_`);
    } else if (t.kind === 'ESCALATE' && deviceId) {
      await srv.escalate(deviceId, t.arg || text).catch((e) => console.warn('escalate:', e.message));
      await toAdmin(`🔐 *בקשה לאישורך* · ${phone} · מכשיר #${deviceId}\n${t.arg || text}\n(בדשבורד → בקשות)`);
    } else if (t.kind === 'ASK' && !res.tags.some((x) => x.kind === 'HUMAN')) {
      await askOwner(chatId, phone, state.chat(chatId).q?.text || text, t.arg);
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
