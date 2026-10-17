'use strict';
/** החלטות קטנות וטהורות (נבדקות בלי וואטסאפ). */

/**
 * כמה לחכות לפני שהבוט עונה — כמו בבוט הישן: מחוץ לשעות — מיד; בשעות הפעילות, אם אתה פעיל
 * (כתבת לאחרונה / קראת את ההתראה) — 5 שניות, כדי שתספיק לענות בעצמך; אחרת — מיד.
 */
function replyDelayMs({ inHours, adminActive, adminRead }) {
  if (!inHours) return 0;
  return adminActive || adminRead ? 5000 : 0;
}

/** כמה לחכות שהלקוח יסיים לכתוב לפני שעונים: לפחות חלון שקט [settleMs] אחרי ההודעה האחרונה,
 *  וגם לא פחות מההמתנה למנהל (replyDelayMs). ככה לא עונים באמצע כשהלקוח שולח כמה הודעות ברצף. */
function settleDelayMs(opts, settleMs = 7000) {
  return Math.max(settleMs, replyDelayMs(opts));
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

module.exports = { replyDelayMs, settleDelayMs, combineBurst, typingMs, parseHashReply, waNumber };
