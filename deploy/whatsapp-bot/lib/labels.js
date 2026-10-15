'use strict';
/**
 * ניהול הבוט מתוך הוואטסאפ עצמו — לפי רשימה (תווית) בוואטסאפ ביזנס, ברירת מחדל "בוט":
 *   • שיחה ברשימה "בוט"  → הבוט עונה בה.
 *   • לקוח חדש            → הבוט מוסיף אותו לרשימה ועונה.
 *   • כתבת בעצמך בשיחה    → הבוט מוציא אותה מהרשימה (זז הצידה).
 *   • להחזיר את הבוט      → בשיחה: הכפתור למעלה (רשימות) → לסמן "בוט".
 * כאן רק ההחלטות (טהור, נבדק ביחידה); הפעולות מול וואטסאפ ב-index.js.
 */

const DEFAULT_NAME = 'בוט';

const norm = (s) => String(s || '').replace(/[‎‏‪-‮]/g, '').trim().toLowerCase();

/** התווית לפי השם (בלי הבדלי רווחים/רישיות/סימני כיוון). */
function findLabel(labels, name) {
  const want = norm(name || DEFAULT_NAME);
  return (labels || []).find((l) => norm(l.name) === want) || null;
}

/** רשימת התוויות של שיחה אחרי הוספה/הסרה של תווית אחת (שאר התוויות נשארות). */
function withLabel(ids, id, on) {
  const s = new Set((ids || []).map(String));
  if (on) s.add(String(id));
  else s.delete(String(id));
  return [...s];
}

/**
 * לקוח חדש = מעולם לא כתבת לו (אין אף הודעה ממך בשיחה). איש קשר שמור בטלפון — לא "חדש" (משפחה, חברים),
 * אלא אם ביקשת גם אותם.
 */
function isNewChat(messages, { isContact = false, includeContacts = false } = {}) {
  if (isContact && !includeContacts) return false;
  return !(messages || []).some((m) => m.fromMe);
}

/**
 * החזרת את הבוט לשיחה (סימנת "בוט") והלקוח מחכה לתשובה? — ההודעה האחרונה ממנו, טקסט, מהזמן האחרון,
 * ועוד לא ענינו עליה.
 */
function needsCatchUp(last, { now = Date.now(), answered = '', windowMs = 15 * 60e3 } = {}) {
  if (!last || last.fromMe || !String(last.body || '').trim()) return false;
  const id = last.id?._serialized || last.id;
  return now - (last.timestamp || 0) * 1000 < windowMs && id !== answered;
}

module.exports = { DEFAULT_NAME, findLabel, withLabel, isNewChat, needsCatchUp };
