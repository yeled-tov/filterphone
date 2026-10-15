'use strict';
/**
 * תיקון לבאג ידוע ב-whatsapp-web.js 1.34.x: אחרי סריקת QR / שחזור חיבור מופיע "authenticated" אבל
 * "ready" לא מגיע אף פעם — והבוט נתקע על "מתחבר" (בוואטסאפ זה נראה מחובר).
 * הסיבה: בזמן הטעינה וואטסאפ ווב יוצר ומוחק iframe זמני; אם זה קורה בדיוק כש-exposeFunction רושם
 * פונקציה בדף — הקריאה נכשלת ב-"Target closed", השגיאה נבלעת, וה-ready לא נשלח.
 *   https://github.com/wwebjs/whatsapp-web.js/issues/201946
 *   https://github.com/wwebjs/whatsapp-web.js/pull/201958 (אותו רעיון — עוד לא שולב בגרסה)
 *
 * התיקון: עוטפים את exposeFunctionIfAbsent בניסיונות חוזרים לשגיאות "זמניות" בלבד. חייב לרוץ לפני
 * require('whatsapp-web.js') (Client.js לוקח את הפונקציה ברגע הטעינה).
 */

const TRANSIENT = /target closed|session closed|frame (was )?detached|execution context was destroyed|detached frame|cannot find context/i;

function isTransient(err) {
  return TRANSIENT.test(String((err && err.message) || err || ''));
}

/** מריץ את fn עד [attempts] פעמים כשהשגיאה זמנית (המתנה עולה), אחרת זורק מיד. */
async function withRetry(fn, { attempts = 6, delayMs = 250, sleep = (ms) => new Promise((r) => setTimeout(r, ms)) } = {}) {
  let last;
  for (let i = 1; i <= attempts; i++) {
    try {
      return await fn();
    } catch (e) {
      last = e;
      if (!isTransient(e) || i === attempts) throw e;
      await sleep(delayMs * i);
    }
  }
  throw last;
}

/**
 * שגיאה בתוך פונקציה שוואטסאפ ווב קורא לה (למשל השלמת הטעינה אחרי החיבור) נבלעת בספרייה בשקט —
 * ככה בדיוק הבוט נתקע על "מתחבר" בלי שום הסבר. כאן היא נכתבת ליומן (ומשם לדשבורד), ונזרקת הלאה כרגיל.
 */
function logged(name, fn) {
  return async (...args) => {
    try {
      return await fn(...args);
    } catch (e) {
      console.error(`[whatsapp-web.js] ${name}: ${String((e && (e.stack || e.message)) || e).slice(0, 500)}`);
      throw e;
    }
  };
}

let applied = false;

function apply() {
  if (applied) return true;
  let mod;
  try {
    mod = require('whatsapp-web.js/src/util/Puppeteer');
  } catch {
    return false;                       // מבנה הספרייה השתנה — בלי התיקון (ה-watchdog עדיין מגן)
  }
  const orig = mod.exposeFunctionIfAbsent;
  if (typeof orig !== 'function') return false;
  mod.exposeFunctionIfAbsent = (page, name, fn) => withRetry(() => orig(page, name, logged(name, fn)));
  applied = true;
  return true;
}

module.exports = { apply, withRetry, isTransient, logged };
