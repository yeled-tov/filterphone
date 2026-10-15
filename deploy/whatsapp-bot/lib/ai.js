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

let modelCache = { key: '', model: '', at: 0 };

async function geminiModel(key, fetchImpl = fetch) {
  if (modelCache.key === key && Date.now() - modelCache.at < 6 * 3600e3) return modelCache.model;
  const r = await fetchImpl(`https://generativelanguage.googleapis.com/v1beta/models?pageSize=200&key=${encodeURIComponent(key)}`);
  if (!r.ok) throw new Error(`gemini models HTTP ${r.status}`);
  const names = ((await r.json()).models || [])
    .filter((m) => (m.supportedGenerationMethods || []).includes('generateContent'))
    .map((m) => String(m.name).replace(/^models\//, ''));
  const model = pickGemini(names);
  modelCache = { key, model, at: Date.now() };
  return model;
}

async function askGemini({ key, model, system, history, text, fetchImpl = fetch }) {
  const m = model || await geminiModel(key, fetchImpl);
  const contents = [...history, { role: 'user', content: text }].map((h) => ({
    role: h.role === 'assistant' ? 'model' : 'user', parts: [{ text: h.content }],
  }));
  const r = await fetchImpl(`https://generativelanguage.googleapis.com/v1beta/models/${m}:generateContent?key=${encodeURIComponent(key)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      systemInstruction: { parts: [{ text: system }] },
      contents,
      generationConfig: { temperature: 0.6, maxOutputTokens: 500 },
    }),
  });
  if (!r.ok) throw new Error(`gemini HTTP ${r.status}`);
  const j = await r.json();
  const out = (j.candidates?.[0]?.content?.parts || []).map((p) => p.text || '').join('').trim();
  if (!out) throw new Error('gemini: empty answer');
  return { text: out, model: m };
}

async function askGroq({ key, model, system, history, text, fetchImpl = fetch }) {
  const m = model || 'llama-3.3-70b-versatile';
  const r = await fetchImpl('https://api.groq.com/openai/v1/chat/completions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${key}` },
    body: JSON.stringify({ model: m, max_tokens: 500, temperature: 0.6,
      messages: [{ role: 'system', content: system }, ...history, { role: 'user', content: text }] }),
  });
  if (!r.ok) throw new Error(`groq HTTP ${r.status}`);
  const out = ((await r.json()).choices?.[0]?.message?.content || '').trim();
  if (!out) throw new Error('groq: empty answer');
  return { text: out, model: m };
}

/** תשובה לפי ההגדרות. בלי מפתח / בכשל — תשובה קבועה ומסירה לנציג (לא שותקים על לקוח). */
async function answer(cfg, ctx, fetchImpl = fetch) {
  const system = systemPrompt(ctx);
  const args = { key: cfg.ai_key, model: cfg.ai_model, system, history: ctx.history, text: ctx.text, fetchImpl };
  try {
    if (cfg.ai_provider === 'gemini' && cfg.ai_key) return { ...parseTags((await askGemini(args)).text), ai: 'gemini' };
    if (cfg.ai_provider === 'groq' && cfg.ai_key) return { ...parseTags((await askGroq(args)).text), ai: 'groq' };
  } catch (e) {
    return { ...fallback(ctx), error: e.message };
  }
  return fallback(ctx);
}

function fallback({ inHours, hoursText, history }) {
  const first = !history.length;
  const text = inHours
    ? (first ? 'שלום! תודה שפנית ל-FilterPhone 🙏 נציג יחזור אליך בהקדם.' : 'קיבלתי, נציג יחזור אליך בהקדם 🙏')
    : `שלום! כעת מחוץ לשעות הפעילות (${hoursText}). נציג יחזור אליך בהקדם 🙏`;
  return { text, tags: [{ kind: 'HUMAN', arg: '' }], ai: 'none' };
}

module.exports = { systemPrompt, parseTags, addressBlock, pickGemini, askGemini, askGroq, answer, fallback,
  _resetModelCache: () => { modelCache = { key: '', model: '', at: 0 }; } };
