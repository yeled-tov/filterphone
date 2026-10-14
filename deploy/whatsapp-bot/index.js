'use strict';
/*
 * גשר וואטסאפ לא-רשמי ל-FilterPhone (whatsapp-web.js).
 *
 * מתחבר לוואטסאפ *שלך* ב-QR (פעם אחת). מאז:
 *   • פניות של לקוחות מהאפליקציה מגיעות אליך כהודעה בצ'אט-עם-עצמך (note to self).
 *   • אתה עונה: "השב" (reply) על אותה הודעה, או "#<מספר מכשיר> טקסט" — והתשובה מגיעה ללקוח באפליקציה.
 *   • אנשים שכותבים לוואטסאפ שלך (לקוחות/קונים) מקבלים מענה אנושי מהבוט — אבל רק כשאינך פעיל
 *     באותה שיחה. ברגע שאתה כותב שם בעצמך, הבוט שותק שם (handoff) למשך חלון זמן.
 *   • הבוט מזהה לקוח לפי הטלפון (CRM) ויכול לבצע חסימה (פעולה בטוחה) לבד; פעולות קריטיות
 *     (השהיה/פתיחה) נרשמות לאישורך בלבד — הבוט לא מבצע אותן.
 *
 * אזהרה: API לא-רשמי של וואטסאפ — בסיכון חסימת מספר. הבוט תוכנן להתנהג כמו קו שירות אמיתי
 * (מגיב בלבד, קצב סביר, handoff לאדם) — לא ל"התחמק" מזיהוי.
 */
const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');

// ---------------------------------------------------------------- הגדרות (משתני סביבה)
const API = (process.env.FP_API || 'https://api.filterphone.com').replace(/\/$/, '');
const BOT_TOKEN = process.env.FP_BOT_TOKEN || '';
const POLL_MS = Number(process.env.FP_POLL_MS || 5000);
const ADMIN_ACTIVE_MS = Number(process.env.FP_ADMIN_ACTIVE_MS || 20 * 60 * 1000); // כמה זמן הבוט שותק אחרי שענית
const GROQ_KEY = process.env.GROQ_API_KEY || '';
const GROQ_MODEL = process.env.GROQ_MODEL || 'llama-3.3-70b-versatile';

if (!BOT_TOKEN) {
  console.error('חסר FP_BOT_TOKEN (צור אותו בדשבורד: מערכת → תמיכה בוואטסאפ → בוט לא-רשמי).');
  process.exit(1);
}

// ---------------------------------------------------------------- לקוח ה-API של FilterPhone
async function api(method, path, body) {
  const res = await fetch(`${API}${path}`, {
    method,
    headers: { 'Authorization': `Bearer ${BOT_TOKEN}`, 'Content-Type': 'application/json', 'Accept': 'application/json' },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  let data = {};
  try { data = text ? JSON.parse(text) : {}; } catch { /* ignore */ }
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

const srv = {
  outbox: (after) => api('GET', `/v1/bot/outbox?after=${after || 0}`),
  forwarded: (ids, waIds) => api('POST', '/v1/bot/forwarded', { ids, wa_ids: waIds }),
  reply: (deviceId, text) => api('POST', '/v1/bot/reply', { device_id: deviceId, text }),
  customer: (phone) => api('GET', `/v1/bot/customer?phone=${encodeURIComponent(phone)}`),
  block: (deviceId, options) => api('POST', '/v1/bot/block', { device_id: deviceId, options }),
  escalate: (deviceId, note) => api('POST', '/v1/bot/escalate', { device_id: deviceId, note }),
};

// ---------------------------------------------------------------- LLM (רשות) — מענה אנושי
let groq = null;
if (GROQ_KEY) { try { groq = new (require('groq-sdk'))({ apiKey: GROQ_KEY }); } catch (e) { console.warn('groq-sdk לא נטען:', e.message); } }

const PERSONA = `את/ה נציג/ת שירות של FilterPhone — שירות ישראלי לסינון תוכן לטלפון (סינון תמונות, חסימת אפליקציות ואתרים, יוטיוב כשר).
דבר/י עברית טבעית, חמה ואנושית — כמו בן אדם אמיתי, לא רובוט. משפטים קצרים, ענייני וחינני, בלי להיות קשוח או יבש.
אפשר לענות גם על שאלות כלליות (מחיר, התקנה, שעות, כתובת) בהיגיון ובנימוס. אל תמציא/י פרטים שאינך בטוח/ה בהם — במקרה כזה אמר/י שנציג יחזור.
אם המשתמש מבקש פעולה על החשבון שלו שאפשר לבצע בבטחה (למשל לחסום אפליקציה/אתר) — סיים/י את התשובה בשורה נפרדת: [[BLOCK:מזהה]] (למשל [[BLOCK:tiktok]]).
אם מבקש/ת פעולה רגישה (להשהות את הסינון, לפתוח/להסיר חסימה, להוריד רמה) — אל תבטיח/י שבוצע; כתוב/י שתעביר/י לאישור, וסיים/י בשורה: [[ESCALATE:תיאור קצר]].
אם לא יודע/ת או שצריך אדם — סיים/י בשורה: [[HUMAN]].`;

async function aiReply(history, text, crm) {
  if (!groq) return { text: 'שלום! קיבלנו את פנייתך ונציג יחזור אליך בהקדם 🙏', tags: [] };
  const ctx = crm ? `\nמידע על הלקוח (פנימי, אל תחשוף מספרים): שם ${crm.customer.name}, ${crm.devices.length} מכשירים.` : '';
  const completion = await groq.chat.completions.create({
    model: GROQ_MODEL, max_tokens: 400, temperature: 0.6,
    messages: [
      { role: 'system', content: PERSONA + ctx },
      ...history.slice(-10),
      { role: 'user', content: text },
    ],
  });
  const raw = completion.choices?.[0]?.message?.content || '';
  const tags = [];
  const clean = raw.replace(/\[\[(BLOCK|ESCALATE|HUMAN):?([^\]]*)\]\]/g, (_, kind, arg) => {
    tags.push({ kind, arg: (arg || '').trim() });
    return '';
  }).trim();
  return { text: clean || 'קיבלתי 🙏', tags };
}

// ---------------------------------------------------------------- מצב בזיכרון
let lastOutboxId = 0;
const waIdToServerMsg = new Map(); // wa message id → server support message id
const adminActiveUntil = new Map(); // chatId → timestamp (handoff: הבוט שותק עד אז)
const chatHistory = new Map();  // chatId → [{role,content}]

function remember(chatId, role, content) {
  const h = chatHistory.get(chatId) || [];
  h.push({ role, content });
  if (h.length > 20) h.shift();
  chatHistory.set(chatId, h);
}

// ---------------------------------------------------------------- לקוח וואטסאפ
const client = new Client({
  authStrategy: new LocalAuth({ dataPath: process.env.FP_SESSION_DIR || './session' }),
  puppeteer: {
    headless: true,
    args: ['--no-sandbox', '--disable-setuid-sandbox'],
    executablePath: process.env.CHROME_PATH || undefined,
  },
});

client.on('qr', (qr) => {
  console.log('\nסרוק את ה-QR מהוואטסאפ שלך (הגדרות → מכשירים מקושרים → קישור מכשיר):\n');
  qrcode.generate(qr, { small: true });
});
client.on('authenticated', () => console.log('אומת ✓'));
client.on('ready', () => { console.log('הבוט מחובר ופעיל ✓'); pollOutbox(); });
client.on('disconnected', (r) => console.warn('נותק:', r));

// ---------------------------------------------------------------- העברת פניות אליך (self chat)
async function pollOutbox() {
  try {
    const { messages } = await srv.outbox(lastOutboxId);
    if (messages && messages.length) {
      const me = client.info?.wid?._serialized;
      const done = [];
      const waIds = {};
      for (const m of messages) {
        const head = `📩 ${m.customer || 'לקוח'}${m.phone ? ' · ' + m.phone : ''} · מכשיר #${m.device_id}`;
        const sent = await client.sendMessage(me, `${head}\n\n${m.text}\n\n↩️ להשיב: "השב" על ההודעה, או כתוב: #${m.device_id} <תשובה>`);
        waIds[m.id] = sent.id._serialized;
        waIdToServerMsg.set(sent.id._serialized, m.device_id);
        done.push(m.id);
        lastOutboxId = Math.max(lastOutboxId, m.id);
      }
      if (done.length) await srv.forwarded(done, waIds);
    }
  } catch (e) {
    console.warn('outbox:', e.message);
  } finally {
    setTimeout(pollOutbox, POLL_MS);
  }
}

// ---------------------------------------------------------------- הודעות נכנסות ויוצאות
client.on('message_create', async (msg) => {
  try {
    const chatId = msg.from === (client.info?.wid?._serialized) ? msg.to : msg.from;
    if (msg.fromMe) return handleAdminMessage(msg);
    return handleIncoming(msg, chatId);
  } catch (e) {
    console.error('message:', e.message);
  }
});

// אתה כתבת (מהוואטסאפ שלך): reply על פנייה / "#id טקסט" → ללקוח; וגם handoff (הבוט שותק בשיחה)
async function handleAdminMessage(msg) {
  const body = (msg.body || '').trim();
  // ל-handoff: אם כתבת בשיחה עם לקוח (לא self), הבוט שותק שם
  const chatId = msg.to;
  if (chatId && chatId !== client.info?.wid?._serialized) {
    adminActiveUntil.set(chatId, Date.now() + ADMIN_ACTIVE_MS);
  }
  // תשובה ללקוח באפליקציה: reply על הודעת פנייה, או "#<deviceId> טקסט"
  let deviceId = null, text = body;
  const hashMatch = body.match(/^#(\d{1,9})\s*[:\-]?\s*([\s\S]*)$/);
  if (hashMatch) { deviceId = Number(hashMatch[1]); text = hashMatch[2].trim(); }
  else if (msg.hasQuotedMsg) {
    const q = await msg.getQuotedMessage().catch(() => null);
    if (q && waIdToServerMsg.has(q.id._serialized)) deviceId = waIdToServerMsg.get(q.id._serialized);
  }
  if (deviceId && text) {
    try { await srv.reply(deviceId, text); console.log(`תשובה ללקוח #${deviceId} נשלחה`); }
    catch (e) { console.warn('reply:', e.message); }
  }
}

// אדם כתב אליך (לקוח/קונה): הבוט עונה — רק אם אינך פעיל באותה שיחה
async function handleIncoming(msg, chatId) {
  if ((adminActiveUntil.get(chatId) || 0) > Date.now()) return; // אתה בשיחה — הבוט שותק
  const text = (msg.body || '').trim();
  if (!text) return;
  remember(chatId, 'user', text);

  // CRM לפי הטלפון (אם זה לקוח קיים)
  let crm = null;
  const phone = chatId.replace(/@.+/, '');
  try { crm = await srv.customer(phone); } catch { /* לא לקוח — עדיין עונים */ }

  const { text: reply, tags } = await aiReply(chatHistory.get(chatId) || [], text, crm);
  if (reply) { await client.sendMessage(chatId, reply); remember(chatId, 'assistant', reply); }

  // פעולות מדורגות — רק ללקוח מזוהה עם מכשיר אחד ברור
  const deviceId = crm?.devices?.length === 1 ? crm.devices[0].device_id : null;
  for (const t of tags) {
    if (!deviceId) { if (t.kind !== 'HUMAN') await client.sendMessage(chatId, 'כדי שאוכל לבצע את זה צריך לזהות את המכשיר — נציג יחזור אליך.'); continue; }
    try {
      if (t.kind === 'BLOCK' && t.arg) { await srv.block(deviceId, [t.arg]); }
      else if (t.kind === 'ESCALATE') { await srv.escalate(deviceId, t.arg || text); await client.sendMessage(chatId, 'העברתי את הבקשה לאישור. נעדכן ברגע שתטופל 🙏'); }
    } catch (e) { console.warn('action', t.kind, e.message); }
  }
}

process.on('unhandledRejection', (e) => console.error('unhandled:', e?.message || e));
client.initialize();
