'use strict';
/**
 * מצב לימוד: מה שהבוט למד מבעל העסק —
 *   qa   — שאלה של לקוח + התשובה שבעל העסק נתן (ענית בעצמך בשיחה, או ענית לבוט כשהוא שאל אותך),
 *   fact — עובדה שלימדת אותו ("למד: ...").
 * כאן רק ההחלטות (טהור, נבדק ביחידה): מה רלוונטי לשאלה, איך זה נכנס להוראות ל-AI, ופקודות הלימוד
 * מהצ'אט עם עצמך. השמירה עצמה בשרת (הדשבורד מציג ומאפשר למחוק / לערוך).
 */

const STOP = new Set(['של', 'את', 'על', 'עם', 'זה', 'זו', 'זאת', 'מה', 'אני', 'אתה', 'את', 'אתם', 'יש', 'אין', 'לי',
  'לך', 'לכם', 'אם', 'או', 'גם', 'כן', 'לא', 'הוא', 'היא', 'הם', 'אבל', 'כי', 'רק', 'עוד', 'כל', 'איך', 'למה',
  'מתי', 'איפה', 'האם', 'שלום', 'היי', 'הי', 'תודה', 'בבקשה', 'אפשר', 'רוצה', 'צריך', 'אצלכם', 'שלכם', 'אתכם',
  'the', 'a', 'an', 'to', 'is', 'are', 'of', 'and', 'in', 'for', 'you', 'i', 'it', 'do', 'can', 'what', 'how']);

/** אותיות שימוש בתחילת מילה (ה/ו/ב/ל/מ/ש/כ) — מורידים עד שתיים, כשנשארת מילה של 3+ אותיות. */
function stem(w) {
  let s = w;
  for (let i = 0; i < 2 && s.length > 3 && /^[הובלמשכ]/.test(s); i++) s = s.slice(1);
  return s;
}

function tokens(text) {
  const words = String(text || '').toLowerCase()
    .replace(/[֑-ׇ]/g, '')                       // ניקוד וטעמים
    .replace(/[^\p{L}\p{N}\s]/gu, ' ')
    .split(/\s+/)
    .filter((w) => w.length >= 2 && !STOP.has(w));
  return [...new Set(words.map(stem).filter((w) => w.length >= 2 && !STOP.has(w)))];
}

/** דמיון בין שתי שאלות (0..1): מילים משותפות ביחס לגודל שתיהן. */
function similarity(a, b) {
  const A = tokens(a);
  const B = new Set(tokens(b));
  if (!A.length || !B.size) return 0;
  const common = A.filter((w) => B.has(w)).length;
  return common / Math.sqrt(A.length * B.size);
}

/**
 * מה מהידע להכניס לתשובה לשאלה הזו: כל העובדות (עד תקרת אורך, החדשות קודם), ועד [k] שאלות דומות
 * שבעל העסק כבר ענה עליהן (מעל סף דמיון; הכי דומה קודם, ובשוויון — העדכנית).
 */
function select(items, question, { k = 6, min = 0.3, factChars = 2500 } = {}) {
  const facts = [];
  let used = 0;
  const all = (items || []).slice().sort((a, b) => (b.updated_at || 0) - (a.updated_at || 0));
  for (const it of all) {
    if (it.kind !== 'fact') continue;
    if (used + it.answer.length > factChars) continue;
    facts.push(it);
    used += it.answer.length;
  }
  const qas = all.filter((it) => it.kind === 'qa')
    .map((it) => ({ it, s: similarity(question, it.question) }))
    .filter((x) => x.s >= min)
    .sort((a, b) => b.s - a.s || (b.it.updated_at || 0) - (a.it.updated_at || 0))
    .slice(0, k)
    .map((x) => x.it);
  return { facts, qas, ids: [...facts, ...qas].map((it) => it.id).filter(Boolean) };
}

/** החלק בהוראות ל-AI. ריק אם אין מה להוסיף. */
function promptSection({ facts = [], qas = [] } = {}) {
  if (!facts.length && !qas.length) return '';
  const out = ['=== מה שלמדת מבעל העסק (גובר על המידע הכללי; אם יש סתירה — העדכני) ==='];
  if (facts.length) {
    out.push('עובדות:');
    for (const f of facts) out.push(`• ${f.answer}`);
  }
  if (qas.length) {
    out.push('שאלות שבעל העסק כבר ענה עליהן — ענה באותו תוכן ובאותה רוח, במילים שלך:');
    for (const q of qas) out.push(`ש: ${q.question}\nת: ${q.answer}`);
    out.push('(אלה תשובות שניתנו ללקוחות אחרים — השתמש רק במידע הכללי שבהן, לעולם לא בפרטים אישיים של לקוח אחר.)');
  }
  out.push('=== סוף מה שלמדת ===');
  return out.join('\n');
}

const ACK = /^(אוקי+|אוקיי+|ok|okay|סבבה|תודה( רבה)?|בסדר|כן|לא|👍+|🙏+|❤️+|מעולה|יופי|נהדר|אחלה|בשמחה)[.!\s]*$/i;
// "רגע, בודק" / "אחזור אליך" — עוד לא תשובה
const WAIT = /^(רגע|שניה|שנייה|דקה|בודק|בודקת|אבדוק|נבדוק|אני בודק|אני אבדוק|אחזור|אחזור אליך|נחזור אליך|תכף|עוד מעט)(?!\p{L})/u;
const PHONE = /(?:\+?972|0)5\d[\s-]?\d{3}[\s-]?\d{4}/;

/** יש בטקסט מספר נייד (פרט אישי) — לא נשמר בידע. */
function hasPhone(text) {
  return PHONE.test(String(text || ''));
}

/**
 * האם תשובה שלך בשיחה עם לקוח שווה ללמוד ממנה: לא אישור קצר ("אוקיי") או "רגע, בודק", בלי מספר טלפון
 * (פרטים אישיים), ועל שאלה של ממש (לא רק "שלום").
 */
function worthLearning(answer, question) {
  const a = String(answer || '').trim();
  if (a.length < 6 || ACK.test(a) || hasPhone(a)) return false;
  if (a.length < 30 && WAIT.test(a)) return false;
  if (question !== undefined && tokens(question).length < 2) return false;
  return true;
}

/**
 * פקודות לימוד בצ'אט עם עצמך:
 *   "למד: המשלוח לוקח 3 ימים"                  → עובדה
 *   "ש: כמה עולה הדרן?\nת: 300 ₪ צריבה..."     → שאלה ותשובה
 *   "שכח 12"                                    → מחיקת פריט 12
 *   "מה למדת"                                   → רשימה
 */
function parseCommand(text) {
  const t = String(text || '').trim();
  let m = t.match(/^ש\s*[:׃]\s*([\s\S]+?)\s*\n\s*ת\s*[:׃]\s*([\s\S]+)$/);
  if (m) return { cmd: 'teach', kind: 'qa', question: m[1].trim(), answer: m[2].trim() };
  m = t.match(/^(?:למד|תלמד)(?:\s*[:׃-]\s*|\s+)([\s\S]{3,})$/);
  if (m) return { cmd: 'teach', kind: 'fact', answer: m[1].trim() };
  m = t.match(/^(?:שכח|תשכח|מחק ידע)\s*#?(\d{1,7})$/);
  if (m) return { cmd: 'forget', id: Number(m[1]) };
  if (/^(?:מה למדת|מה למדת\?|ידע|📚)$/.test(t)) return { cmd: 'list' };
  return null;
}

/** שורה קצרה לתצוגה בוואטסאפ. */
function short(text, n = 90) {
  const t = String(text || '').replace(/\s+/g, ' ').trim();
  return t.length > n ? `${t.slice(0, n - 1)}…` : t;
}

/** רשימת מה שהבוט למד — לצ'אט עם עצמך ("מה למדת"). */
function listText(items, { max = 15 } = {}) {
  if (!items?.length) return '📚 *עוד לא למדתי כלום.*\n\nאני לומד מהתשובות שלך ללקוחות, ומהתשובות שלך כשאני שואל אותך. אפשר גם ללמד אותי ישירות — כתוב *8*.';
  const lines = items.slice(0, max).map((it) => (it.kind === 'fact'
    ? `*#${it.id}* 📌 ${short(it.answer)}`
    : `*#${it.id}* ש: ${short(it.question, 60)}\n      ת: ${short(it.answer)}`));
  const more = items.length > max ? `\n\n…ועוד ${items.length - max} — הכול בדשבורד (בוט וואטסאפ → מה הבוט למד).` : '';
  return `📚 *מה שלמדתי* (${items.length})\n\n${lines.join('\n')}${more}\n\n🗑️ למחוק: *שכח <מספר>* (למשל: שכח ${items[0].id})`;
}

const TEACH_HELP = `✏️ *איך ללמד אותי* (כאן, בצ'אט עם עצמך)

📌 עובדה:
למד: משלוח לכל הארץ תוך 3 ימי עסקים

❔ שאלה ותשובה (בשתי שורות):
ש: כמה עולה סינון למכשיר?
ת: 250 ₪ חד פעמי, כולל התקנה

🤖 אני לומד גם לבד:
• תשובה שלך ללקוח בשיחה — אני זוכר אותה לשאלה שלו
• כשאני לא יודע — אני שואל כאן; "השב" על השאלה שלי עם התשובה, ואני שולח ללקוח וזוכר

📚 *מה למדת* — הרשימה · 🗑️ *שכח 12* — מחיקה`;

module.exports = { tokens, similarity, select, promptSection, hasPhone, worthLearning, parseCommand, short, listText,
  TEACH_HELP };
