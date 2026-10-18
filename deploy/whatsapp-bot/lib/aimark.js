'use strict';
/**
 * הסימון "✦ AI" של וואטסאפ ליד השעה (כמו בעסקים שעונים עם AI): ההודעה נשלחת כ"הודעה אוטומטית של
 * העסק" — bizBotType ב-וואטסאפ ווב, שמוסיף לה את התגית <bot local_automated_type> והלקוח רואה AI.
 * וואטסאפ דחה את ההודעה (חשבון שלא מורשה)? חוזרים לסימון בטקסט (ai_tag) ולא מנסים שוב שבוע.
 * כאן רק ההחלטות (טהור, נבדק ביחידה); השליחה ב-index.js.
 */

const BIZ_BOT_TYPE = 'biz_1p';
const RETRY_AFTER_MS = 7 * 24 * 3600e3;

/** לשלוח עם הסימון המקורי של וואטסאפ? (מופעל בדשבורד, ולא נכשל בשבוע האחרון) */
function nativeOn(cfg, failedAt = 0, now = Date.now()) {
  return cfg?.mark_ai !== false && (!failedAt || now - failedAt > RETRY_AFTER_MS);
}

/** בלי הסימון המקורי: הטקסט מהדשבורד בסוף ההודעה (אם הוגדר). */
function withTextTag(cfg, text) {
  const tag = String(cfg?.ai_tag || '').trim();
  return cfg?.mark_ai !== false && tag ? `${text}\n\n${tag}` : text;
}

module.exports = { BIZ_BOT_TYPE, RETRY_AFTER_MS, nativeOn, withTextTag };
