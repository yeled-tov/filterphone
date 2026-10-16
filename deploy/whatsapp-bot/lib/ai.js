'use strict';
/**
 * "המוח" של הבוט: Gemini (ברירת מחדל) או Groq. ההוראות נבנות ממידע העסק שבדשבורד ומה שהבוט למד
 * ממך, והבוט מסמן פעולות בתגיות בסוף התשובה — [[ADDRESS]] / [[HUMAN]] / [[BLOCK:מזהה]] /
 * [[ESCALATE:תיאור]] / [[ASK:מה חסר]] (לא יודע — שואל אותך בצ'אט עם עצמך).
 */

function systemPrompt({ botName, businessInfo, inHours, hoursText, crm, knowledge = '', askWhenUnsure = false }) {
  const who = crm
    ? `\nהפונה הוא לקוח קיים: ${crm.customer.name || 'לקוח'}, ${crm.devices.length} מכשירים. אל תחשוף מספרים או פרטים פנימיים.`
    : '';
  const unsure = askWhenUnsure
    ? 'אם חסר לך מידע כדי לענות (מחיר, זמינות, פרט שלא מופיע למעלה) — אל תנחש/י. כתוב/כתבי ללקוח בנימוס שאת/ה בודק/ת ותחזור/י אליו, וסיים/י ב-[[ASK:מה בדיוק חסר לך]].'
    : 'אם אינך יודע/ת — אמור/אמרי שנציג יחזור.';
  const askTag = askWhenUnsure
    ? '\n• חסר לך מידע כדי לענות → [[ASK:השאלה שלך לבעל העסק]] (במקום [[HUMAN]], אלא אם הלקוח ביקש נציג)'
    : '';
  return `את/ה "${botName || 'פילטר'}", נציג/ת שירות של FilterPhone. עונים ללקוחות ולמתעניינים בוואטסאפ.
כתוב/כתבי בעברית טבעית, חמה ואנושית — כמו בן אדם אמיתי, לא רובוט. משפטים קצרים, אדיבים, ענייניים ומכבדים.
${inHours ? 'כעת שעות פעילות — נציג זמין בהמשך אם צריך.' : `כעת מחוץ לשעות הפעילות (${hoursText}). בפנייה ראשונה ציין/צייני בעדינות שנציג יחזור בשעות הפעילות.`}
בהודעה הראשונה בשיחה — ברכה קצרה ושאלה "במה אפשר לעזור?".
ענה/עני רק לפי המידע הבא. אל תמציא/י מחירים, מבצעים או הבטחות. ${unsure}

=== מידע העסק ===
${businessInfo}
=== סוף המידע ===
${knowledge ? `\n${knowledge}\n` : ''}${who}
תגיות (בשורה נפרדת בסוף, רק כשצריך):
• שואלים על כתובת / איך מגיעים → [[ADDRESS]]
• מבקשים נציג, התקנה, תיאום, או מחיר מדויק שלא מופיע במידע → [[HUMAN]]
• לקוח קיים מבקש לחסום אפליקציה/אתר בטלפון שלו → [[BLOCK:מזהה]] (למשל [[BLOCK:tiktok]])
• לקוח מבקש פעולה רגישה (להשהות סינון, לפתוח חסימה, להוריד רמה) → אל תבטיח/י שבוצע; כתוב/כתבי שזה עובר לאישור, וסיים/י ב-[[ESCALATE:תיאור קצר]]${askTag}`;
}

const TAG = /\[\[(ADDRESS|HUMAN|BLOCK|ESCALATE|ASK)(?::([^\]]*))?\]\]/g;

/** מפריד בין הטקסט ללקוח לבין התגיות. תומך גם במילים הישנות ADDRESS / HANDOFF. */
function parseTags(raw) {
  const tags = [];
  let text = String(raw || '').replace(TAG, (_, kind, arg) => {
    tags.push({ kind, arg: (arg || '').trim() });
    return '';
  });
  text = text.replace(/\bHANDOFF\b/g, () => { tags.push({ kind: 'HUMAN', arg: '' }); return ''; })
    .replace(/\bADDRESS\b/g, () => { tags.push({ kind: 'ADDRESS', arg: '' }); return ''; });
  return { text: text.replace(/\n{3,}/g, '\n\n').trim(), tags };
}

/** הכתובת מתוך מידע העסק (שורה שמתחילה ב"כתובת:") + קישורי ניווט. */
function addressBlock(businessInfo) {
  const m = /^\s*כתובת\s*:\s*(.+)$/m.exec(businessInfo || '');
  if (!m) return '';
  const addr = m[1].replace(/[.\s]+$/, '');
  const q = encodeURIComponent(addr);
  return `📍 *הכתובת שלנו:*\n${addr}\n\n🚗 Waze:\nhttps://waze.com/ul?q=${q}&navigate=yes\n\n🗺️ Google Maps:\nhttps://www.google.com/maps/search/?api=1&query=${q}`;
}

/** המודל ה"flash" היציב העדכני ביותר (כמו בשרת — wabot.pick_gemini). */
function pickGemini(names) {
  const ver = (n) => { const m = /^gemini-(\d+(?:\.\d+)?)-flash$/.exec(n); return m ? Number(m[1]) : -1; };
  const stable = names.filter((n) => ver(n) >= 0);
  if (stable.length) return stable.sort((a, b) => ver(b) - ver(a))[0];
  const flash = names.filter((n) => n.includes('flash') && !n.includes('exp') && !n.includes('preview')).sort();
  return flash.length ? flash[flash.length - 1] : (names[0] || '');
}

// --- קריאות AI: זמן קצוב, ניסיון חוזר ומודל גיבוי ---------------------------------------------
// Gemini מחזיר לפעמים 503 (המודל עמוס) / 429 / ניתוק רשת. בלי ניסיון חוזר כל עומס כזה הפך לתשובה
// קבועה ("נציג יחזור אליך") — והבוט נראה "טיפש". עכשיו: עוד ניסיון אחרי הפסקה קצרה, ואז מודל גיבוי.
const AI_TIMEOUT_MS = Number(process.env.FP_AI_TIMEOUT_MS) || 15000;    // לכל ניסיון
const AI_BUDGET_MS = Number(process.env.FP_AI_BUDGET_MS) || 35000;      // לכל התשובה
const RETRY_MS = Number(process.env.FP_AI_RETRY_MS) || 700;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

class AiHttpError extends Error {
  constructor(provider, status) { super(`${provider} HTTP ${status}`); this.status = status; }
}

/** שווה לנסות שוב (עומס / מגבלת קצב / שגיאת שרת / רשת / פסק זמן / תשובה ריקה). 400/401/403 — לא. */
function retryable(e) {
  return !e.status || e.status === 404 || e.status === 408 || e.status === 429 || e.status >= 500;
}

/** fetch עם timeout (AbortController). בכשל/פסק-זמן נזרקת שגיאה. */
function tfetch(fetchImpl, url, opts = {}) {
  const ac = new AbortController();
  const timer = setTimeout(() => ac.abort(), AI_TIMEOUT_MS);
  return Promise.resolve(fetchImpl(url, { ...opts, signal: ac.signal }))
    .finally(() => clearTimeout(timer));
}

let modelCache = { key: '', names: [], at: 0 };

/** רשימת המודלים של Gemini שתומכים ב-generateContent (נשמרת 6 שעות). */
async function geminiNames(key, fetchImpl = fetch) {
  if (modelCache.key === key && modelCache.names.length && Date.now() - modelCache.at < 6 * 3600e3) return modelCache.names;
  const r = await tfetch(fetchImpl, `https://generativelanguage.googleapis.com/v1beta/models?pageSize=200&key=${encodeURIComponent(key)}`);
  if (!r.ok) throw new AiHttpError('gemini models', r.status);
  const names = ((await r.json()).models || [])
    .filter((m) => (m.supportedGenerationMethods || []).includes('generateContent'))
    .map((m) => String(m.name).replace(/^models\//, ''));
  modelCache = { key, names, at: Date.now() };
  return names;
}

async function geminiModel(key, fetchImpl = fetch) {
  return pickGemini(await geminiNames(key, fetchImpl));
}

/** מודלי גיבוי לפי סדר: flash יציבים (החדש קודם), ואז flash-lite יציבים. */
function geminiFallbacks(names, exclude) {
  const ver = (n, re) => { const m = re.exec(n); return m ? Number(m[1]) : -1; };
  const flash = /^gemini-(\d+(?:\.\d+)?)-flash$/;
  const lite = /^gemini-(\d+(?:\.\d+)?)-flash-lite$/;
  const by = (re) => names.filter((n) => ver(n, re) >= 0).sort((a, b) => ver(b, re) - ver(a, re));
  return [...by(flash), ...by(lite)].filter((n) => n !== exclude);
}

async function geminiGenerate(model, key, body, fetchImpl) {
  const r = await tfetch(fetchImpl, `https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent?key=${encodeURIComponent(key)}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body,
  });
  if (!r.ok) throw new AiHttpError('gemini', r.status);
  const j = await r.json();
  const out = (j.candidates?.[0]?.content?.parts || []).map((p) => p.text || '').join('').trim();
  if (!out) throw new Error('gemini: empty answer');
  return { text: out, model };
}

/**
 * Gemini עם ניסיון חוזר: המודל שנבחר → אותו מודל שוב אחרי הפסקה קצרה (עומס חולף) → עד שני מודלי גיבוי.
 * עד 4 ניסיונות ובתוך AI_BUDGET_MS, כדי שהלקוח לא יחכה לנצח.
 */
async function askGemini({ key, model, system, history, text, fetchImpl = fetch }) {
  const contents = [...history, { role: 'user', content: text }].map((h) => ({
    role: h.role === 'assistant' ? 'model' : 'user', parts: [{ text: h.content }],
  }));
  const body = JSON.stringify({
    systemInstruction: { parts: [{ text: system }] },
    contents,
    generationConfig: { temperature: 0.6, maxOutputTokens: 500 },
  });
  const started = Date.now();
  const first = model || await geminiModel(key, fetchImpl);
  const plan = [first, first];
  let lastErr = null;
  let fallbacksAdded = false;
  for (let i = 0; i < plan.length && i < 4; i++) {
    if (i > 0) {
      if (Date.now() - started > AI_BUDGET_MS) break;
      if (plan[i] === plan[i - 1]) await sleep(RETRY_MS);
    }
    try {
      return await geminiGenerate(plan[i], key, body, fetchImpl);
    } catch (e) {
      lastErr = e;
      if (!retryable(e)) throw e;
      if (e.status === 404 && plan[i + 1] === plan[i]) plan.splice(i + 1, 1);      // המודל לא קיים — לא חוזרים אליו
      if (!fallbacksAdded) {
        fallbacksAdded = true;
        const names = await geminiNames(key, fetchImpl).catch(() => []);
        plan.push(...geminiFallbacks(names, first).slice(0, 2));
      }
    }
  }
  throw lastErr || new Error('gemini: no model answered');
}

async function askGroq({ key, model, system, history, text, fetchImpl = fetch }) {
  const m = model || 'llama-3.3-70b-versatile';
  const body = JSON.stringify({ model: m, max_tokens: 500, temperature: 0.6,
    messages: [{ role: 'system', content: system }, ...history, { role: 'user', content: text }] });
  let lastErr = null;
  for (let i = 0; i < 2; i++) {
    if (i > 0) await sleep(RETRY_MS);
    try {
      const r = await tfetch(fetchImpl, 'https://api.groq.com/openai/v1/chat/completions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${key}` },
        body,
      });
      if (!r.ok) throw new AiHttpError('groq', r.status);
      const out = ((await r.json()).choices?.[0]?.message?.content || '').trim();
      if (!out) throw new Error('groq: empty answer');
      return { text: out, model: m };
    } catch (e) {
      lastErr = e;
      if (!retryable(e)) throw e;
    }
  }
  throw lastErr;
}

/**
 * תשובה לפי ההגדרות.
 *   • בלי מפתח — תשובה קבועה ומסירה לנציג (HUMAN), כמו קודם.
 *   • ה-AI לא זמין (גם אחרי ניסיונות חוזרים) — תשובה נעימה ללקוח ושאלה לבעל העסק (ASK), *בלי* להוציא
 *     את הבוט מהשיחה: בהודעה הבאה הוא מנסה שוב כרגיל.
 */
async function answer(cfg, ctx, fetchImpl = fetch) {
  const system = systemPrompt(ctx);
  const args = { key: cfg.ai_key, model: cfg.ai_model, system, history: ctx.history, text: ctx.text, fetchImpl };
  try {
    if (cfg.ai_provider === 'gemini' && cfg.ai_key) return { ...parseTags((await askGemini(args)).text), ai: 'gemini' };
    if (cfg.ai_provider === 'groq' && cfg.ai_key) return { ...parseTags((await askGroq(args)).text), ai: 'groq' };
  } catch (e) {
    return { ...fallback(ctx, { outage: true }), error: e.message };
  }
  return fallback(ctx);
}

function fallback({ inHours, hoursText, history }, { outage = false } = {}) {
  const first = !history.length;
  if (outage) {
    const text = inHours
      ? (first ? 'שלום! תודה שפנית ל-FilterPhone 🙏 אני בודק ואחזור אליך בהקדם.' : 'קיבלתי 🙏 בודק ואחזור אליך בהקדם.')
      : `שלום! כעת מחוץ לשעות הפעילות (${hoursText}). קיבלנו את ההודעה ונחזור אליך בהקדם 🙏`;
    return { text, tags: [{ kind: 'ASK', arg: 'ה-AI לא היה זמין לרגע — אפשר לענות ללקוח כאן' }], ai: 'none' };
  }
  const text = inHours
    ? (first ? 'שלום! תודה שפנית ל-FilterPhone 🙏 נציג יחזור אליך בהקדם.' : 'קיבלתי, נציג יחזור אליך בהקדם 🙏')
    : `שלום! כעת מחוץ לשעות הפעילות (${hoursText}). נציג יחזור אליך בהקדם 🙏`;
  return { text, tags: [{ kind: 'HUMAN', arg: '' }], ai: 'none' };
}

module.exports = { systemPrompt, parseTags, addressBlock, pickGemini, geminiFallbacks, askGemini, askGroq, answer, fallback,
  _resetModelCache: () => { modelCache = { key: '', names: [], at: 0 }; } };
