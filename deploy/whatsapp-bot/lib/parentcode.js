'use strict';
/**
 * קוד הורים: לקוח שהגדיר קוד — כל שינוי בחסימות (חסימה / פתיחה זמנית) דורש שיקליד אותו בשיחה.
 * השרת בודק את הקוד (ונועל אחרי ניסיונות שגויים) — הבוט לא יכול לעקוף. הבוט רק שומר את הפעולה
 * שממתינה לקוד, ומעביר את הקוד לשרת. הקוד לא נכנס להיסטוריה של ה-AI ולא נשלח ל-AI.
 */

const TTL_MS = 5 * 60_000;             // פעולה שממתינה לקוד — עד 5 דקות

const ASK = '🔐 כדי לבצע את השינוי צריך את *קוד ההורים*.\nהקלידו כאן את הקוד (רק הספרות).';
const WRONG = '❌ הקוד שגוי. נסו שוב (רק הספרות), או פנו להורים.';
const LOCKED = '🔒 יותר מדי ניסיונות שגויים לקוד ההורים — השינוי נחסם ל-15 דקות.';

/** הקוד מתוך הודעה: רק ספרות (4–8), מותר רווחים / מקפים / סימני כיוון. אחרת null. */
function extract(text) {
  const t = String(text || '').replace(/[\s\-\u200e\u200f\u202a-\u202e]/g, '');
  return /^\d{4,8}$/.test(t) ? t : null;
}

/** השרת דחה כי חסר / שגוי קוד הורים (התשובה כוללת need=parent_code). */
function isCodeError(e) {
  return e?.need === 'parent_code';
}

/** ההודעה ללקוח לפי סוג הדחייה. */
function message(errs) {
  if (errs.some((e) => e.locked)) return LOCKED;
  if (errs.some((e) => e.wrong)) return WRONG;
  return ASK;
}

/** פעולות שממתינות לקוד, לפי שיחה. */
function store(now = () => Date.now()) {
  const m = new Map();
  return {
    set(key, value) { m.set(key, { ...value, at: now() }); },
    get(key) {
      const v = m.get(key);
      if (v && now() - v.at > TTL_MS) { m.delete(key); return null; }
      return v || null;
    },
    take(key) { const v = this.get(key); m.delete(key); return v; },
  };
}

module.exports = { extract, isCodeError, message, store, ASK, WRONG, LOCKED, TTL_MS };
