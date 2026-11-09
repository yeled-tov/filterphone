'use strict';
/** החלטות קטנות וטהורות (נבדקות בלי וואטסאפ). */

/**
 * כמה לחכות לפני שהבוט עונה: מחוץ לשעות הפעילות — מיד. בשעות הפעילות — waitMs (ברירת מחדל 2 דקות),
 * כדי שתספיק לענות בעצמך (ענית — הבוט זז הצידה). engaged — הבוט כבר באמצע שיחה עם הלקוח הזה (ענה
 * לו לפני רגע ואתה לא התערבת) — ממשיך מיד, בלי לחכות שוב על כל הודעה.
 */
function replyDelayMs({ inHours, waitMs = DEFAULT_WAIT_MS, engaged = false }) {
  if (!inHours || engaged) return 0;
  return Math.max(0, Number.isFinite(waitMs) ? waitMs : DEFAULT_WAIT_MS);
}

/** בשעות הפעילות — כמה זמן מחכים שהמנהל יענה בעצמו (ברירת מחדל; בדשבורד: wait_seconds). */
const DEFAULT_WAIT_MS = 2 * 60e3;

/** wait_seconds מהדשבורד → מילישניות (חסר / לא תקין = ברירת המחדל). */
function waitMsFrom(cfg) {
  const s = Number(cfg?.wait_seconds);
  return Number.isFinite(s) && s >= 0 ? s * 1000 : DEFAULT_WAIT_MS;
}

/**
 * שיחה שלא ברשימת הבוט ולא ענית לה — מתי הבוט עונה במקומך:
 *   • בשעות הפעילות — אחרי [minutes] (ברירת מחדל 45 דקות; בדשבורד: unlisted_minutes).
 *   • מחוץ לשעות — אחרי דקה (שתספיק אם אתה בדיוק עונה), אלא אם כתבת בשיחה הזו בעצמך בזמן האחרון
 *     ([ownerAt] בתוך [recentMs]) — אז אתה באמצע שיחה איתו, ומחכים כמו בשעות הפעילות.
 */
function unlistedDelayMs({ inHours, minutes, ownerAt = 0, now = Date.now(), recentMs = 60 * 60e3 }) {
  const m = Number(minutes);
  const full = (Number.isFinite(m) && m > 0 ? m : DEFAULT_UNLISTED_MIN) * 60e3;
  if (inHours || (ownerAt && now - ownerAt < recentMs)) return full;
  return UNLISTED_OFF_HOURS_MS;
}

const DEFAULT_UNLISTED_MIN = 45;
const UNLISTED_OFF_HOURS_MS = 60e3;

/**
 * לצרף את מדריך ההתקנה המלא לפרומפט? ב-Gemini — תמיד (חלון ענק, זול). ב-Groq (מכסת מילים לדקה בתוכנית
 * החינמית) — רק כשהשיחה נוגעת בהתקנה / תקלה / צילום מסך.
 */
function wantsInstallGuide(provider, text) {
  if (provider !== 'groq') return true;
  return INSTALL_TOPIC.test(String(text || ''));
}

const INSTALL_TOPIC = new RegExp([
  'התקנ', 'להתקין', 'מתקין', 'הורד', 'להוריד', 'הורדה', 'apk', 'play ?protect', 'פליי פרוטקט', 'חסימה אוטומטית',
  'auto ?blocker', 'אפשרויות מפתחים', 'מפתחים', 'ניפוי', 'debug', 'חשבון', 'account', 'שגיאה', 'error', 'לא מצליח',
  'לא עובד', 'נתקע', 'קוד התאמה', 'סמסונג', 'גלקסי', 'galaxy', 'samsung', 'שיאומי', 'שאומי', 'xiaomi', 'רדמי',
  'redmi', 'פוקו', 'poco', 'אנדרואיד', 'android', 'אייפון', 'iphone', 'דגם', 'מכשיר', '📷',
].join('|'), 'i');

/** "בעוד 2 דקות" / "בעוד 30 שניות" — להודעה אליך. */
function waitText(ms) {
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s} שניות`;
  const m = Math.round(s / 30) / 2;
  return m === 1 ? 'דקה' : `${m} דקות`;
}

/** הודעה קולית (או קובץ שמע) בלי כיתוב — מתמללים אותה, והבוט עונה בכתב. */
function isVoice(msg) {
  return Boolean(msg) && (msg.type === 'ptt' || msg.type === 'audio') && !String(msg.body || '').trim();
}

/**
 * סוג המדיה בהודעה: voice (הודעה קולית / שמע בלי כיתוב), image (תמונה / צילום מסך / מסמך), video,
 * או null (טקסט, מדבקה, מיקום...). לפיו הבוט מתמלל / מתאר, או מבקש לתאר במילים.
 */
function mediaKind(msg) {
  if (!msg) return null;
  if (isVoice(msg)) return 'voice';
  if (msg.type === 'image' || msg.type === 'document') return 'image';
  if (msg.type === 'video') return 'video';
  return null;
}

/** כמה הודעות רצופות של הלקוח = שאלה אחת: מחברים לפסקאות, בלי כפילויות עוקבות ובלי ריקות. */
function combineBurst(parts) {
  const out = [];
  for (const p of parts) {
    const t = String(p || '').trim();
    if (t && t !== out[out.length - 1]) out.push(t);
  }
  return out.join('\n');
}

/** השהיה "אנושית" לפני שליחה (עם "מקליד…"): לפי אורך התשובה, 1.2–6 שניות. */
function typingMs(text) {
  return Math.round(Math.min(6000, 1200 + String(text || '').length * 25));
}

/** "#123 טקסט" (תשובה ללקוח באפליקציה) → {deviceId, text} או null. */
function parseHashReply(body) {
  const m = /^#(\d{1,9})\s*[:\-]?\s*([\s\S]+)$/.exec(String(body || '').trim());
  return m ? { deviceId: Number(m[1]), text: m[2].trim() } : null;
}

/** מספר טלפון ישראלי בפורמט וואטסאפ: 0527186881 → 972527186881. */
function waNumber(raw) {
  let d = String(raw || '').replace(/\D/g, '');
  if (d.startsWith('0')) d = '972' + d.slice(1);
  return d;
}

/** שיחה פרטית עם אדם (לא קבוצה / ערוץ / רשימת תפוצה / סטטוס). */
function isPersonChat(chatId) {
  return /@(c\.us|lid)$/.test(String(chatId || ''));
}

module.exports = { replyDelayMs, waitMsFrom, waitText, DEFAULT_WAIT_MS, combineBurst, typingMs, parseHashReply, waNumber,
  isVoice, mediaKind, unlistedDelayMs, wantsInstallGuide, isPersonChat, UNLISTED_OFF_HOURS_MS };
