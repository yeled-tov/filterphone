'use strict';
/**
 * "המוח" של הבוט: Gemini (ברירת מחדל) או Groq. ההוראות נבנות ממידע העסק שבדשבורד ומה שהבוט למד
 * ממך, והבוט מסמן פעולות בתגיות בסוף התשובה — [[ADDRESS]] / [[HUMAN]] / [[BLOCK:מזהה]] /
 * [[OPEN:מזהה:דקות]] / [[CLOSE:מזהה]] / [[ESCALATE:תיאור]] / [[ASK:מה חסר]] (לא יודע — שואל אותך).
 * מה מותר לחסום / לפתוח — רשימה סגורה מהשרת לכל מכשיר (lib/actions.js).
 */

function systemPrompt({ botName, businessInfo, inHours, hoursText, crm, knowledge = '', askWhenUnsure = false, actions = '',
  otherChannel = '' }) {
  const who = crm
    ? `\nהפונה הוא לקוח קיים: ${crm.customer.name || 'לקוח'}, ${crm.devices.length} מכשירים. אל תחשוף מספרים או פרטים פנימיים.`
    : '';
  const unsure = askWhenUnsure
    ? 'אם חסר לך מידע כדי לענות (מחיר, זמינות, פרט שלא מופיע למעלה) — אל תנחש/י. כתוב/כתבי ללקוח בנימוס שאת/ה בודק/ת ותחזור/י אליו, וסיים/י ב-[[ASK:מה בדיוק חסר לך]].'
    : 'אם אינך יודע/ת — אמור/אמרי שנציג יחזור.';
  const askTag = askWhenUnsure
    ? '\n• חסר לך מידע כדי לענות → [[ASK:השאלה שלך לבעל העסק]] (במקום [[HUMAN]], אלא אם הלקוח ביקש נציג)'
    : '';
  return `את/ה "${botName || 'פילטר'}", נציג/ת שירות של FilterPhone. עונים ללקוחות ולמתעניינים בוואטסאפ ובצ'אט התמיכה שבאפליקציה.
כתוב/כתבי בעברית טבעית, חמה ואנושית — כמו בן אדם אמיתי, לא רובוט. משפטים קצרים, אדיבים, ענייניים ומכבדים.
${inHours
    ? 'כעת שעות פעילות. ענה/עני ועזור/עזרי עד הסוף בעצמך; נציג אנושי רק כשבאמת צריך (התקנה, תיאום הגעה, תשלום).'
    : `כעת מחוץ לשעות הפעילות (${hoursText}) — ובשביל זה את/ה כאן: עזור/עזרי ללקוח עד הסוף, בדיוק כמו בשעות הפעילות. אל תדחה/תדחי אותו ל"שעות הפעילות" ואל תפתח/תפתחי בזה. רק כשמשהו באמת דורש בן אדם (התקנה, תיאום הגעה, תשלום) — עזור/עזרי בכל מה שאפשר עכשיו, וכתוב/כתבי שנציג ימשיך בשעות הפעילות.`}
בהודעה הראשונה בשיחה — ברכה קצרה, ואם הלקוח כבר שאל משהו — ישר לעניין.
הבן/י מה הלקוח באמת צריך, גם כשהניסוח לא מדויק או כתוב בשגיאות. בעזרה טכנית — הוראות ברורות צעד אחר צעד. חסר פרט חיוני? שאלה אחת קצרה.
הודעת לקוח שמתחילה ב-🎤 היא תמלול אוטומטי של הודעה קולית — ייתכנו בה טעויות תמלול; הבן/י לפי ההקשר וענה/עני בכתב כרגיל. אם התמלול לא הצליח — בקש/י בנימוס לכתוב את הבקשה במילים.
הודעת לקוח שמתחילה ב-📷 כוללת תיאור אוטומטי של תמונה ששלח (בדרך כלל צילום מסך מהטלפון: הודעת שגיאה, הגדרה, אפליקציה) — התייחס/י לתוכן שלה ועזור/עזרי לפיו. אם לא הצלחנו לראות אותה — בקש/י לתאר במילים מה מופיע במסך.
ענה/עני רק לפי המידע הבא. אל תמציא/י מחירים, מבצעים או הבטחות. ${unsure}

=== מידע העסק ===
${businessInfo}
=== סוף המידע ===
${knowledge ? `\n${knowledge}\n` : ''}${otherChannel ? `\n${otherChannel}\n` : ''}${who}
${actions ? `\n${actions}\n` : ''}
תגיות (בשורה נפרדת בסוף, רק כשצריך):
• שואלים על כתובת / איך מגיעים → [[ADDRESS]]
• מבקשים נציג, התקנה, תיאום, או מחיר מדויק שלא מופיע במידע → [[HUMAN]]
${actions ? '• חסימה / פתיחה זמנית / סגירה — לפי הרשימות למעלה ([[BLOCK]] / [[OPEN]] / [[CLOSE]])' : '• לקוח קיים מבקש לחסום אפליקציה/אתר בטלפון שלו → [[BLOCK:מזהה]] (למשל [[BLOCK:tiktok]])'}
• לקוח מבקש פעולה רגישה שלא ברשימות (להשהות סינון, פתיחה קבועה, להוריד רמה) → אל תבטיח/י שבוצע; כתוב/כתבי שזה עובר לאישור, וסיים/י ב-[[ESCALATE:תיאור קצר]]${askTag}`;
}

const TAG = /\[\[(ADDRESS|HUMAN|BLOCK|OPEN|CLOSE|ESCALATE|ASK)(?::([^\]]*))?\]\]/g;

/**
 * מה שהלקוח כתב לאחרונה בערוץ השני (וואטסאפ ↔ צ'אט התמיכה באפליקציה) — כדי שהבוט ימשיך מאותה נקודה
 * ולא ישאל שוב מה כבר נאמר. ריק כשאין.
 */
function otherChannelSection(label, history, max = 6) {
  const turns = (history || []).slice(-max).filter((h) => h && h.content);
  if (!turns.length) return '';
  const lines = turns.map((h) => `${h.role === 'user' ? 'לקוח' : 'אנחנו'}: ${String(h.content).slice(0, 300)}`);
  return `=== מה שנאמר לאחרונה עם הלקוח ב${label} (להקשר — אל תחזור/תחזרי על מה שכבר נענה) ===\n${lines.join('\n')}\n=== סוף ===`;
}

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
function tfetch(fetchImpl, url, opts = {}, ms = AI_TIMEOUT_MS) {
  const ac = new AbortController();
  const timer = setTimeout(() => ac.abort(), ms);
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

// מגבלת האורך כוללת גם את ה"חשיבה" של המודל (Gemini 2.5 ומעלה). עם 500 החשיבה אכלה את רוב התקציב
// והתשובה נחתכה באמצע מילה — וגם התגית שבסוף (פעולה) נחתכה ולא בוצעה. עכשיו: הרבה מקום, ובמודלים
// המהירים בלי חשיבה בכלל (מהיר יותר — הלקוח מקבל תשובה מיד).
const MAX_OUTPUT_TOKENS = 2048;

/** הגדרת החשיבה לפי המודל: flash / flash-lite של 2.5 — כבויה; 3.x flash — מינימלית; אחרים — ברירת מחדל. */
function thinkingFor(model) {
  if (/^gemini-2\.5-flash/.test(model)) return { thinkingBudget: 0 };
  if (/^gemini-3(\.\d+)?-flash/.test(model)) return { thinkingLevel: 'minimal' };
  return null;
}

/** תשובה שנחתכה (MAX_TOKENS): עד סוף המשפט השלם האחרון — לא שולחים חצי מילה ללקוח. */
function trimCut(text) {
  const t = String(text || '').trim();
  const cut = Math.max(t.lastIndexOf('.'), t.lastIndexOf('!'), t.lastIndexOf('?'), t.lastIndexOf('\n'), t.lastIndexOf('🙏'));
  return cut > t.length * 0.4 ? t.slice(0, cut + 1).trim() : t;
}

function geminiBody({ system, contents, temperature = 0.6, maxTokens = MAX_OUTPUT_TOKENS }, model, withThinking = true) {
  const thinking = withThinking ? thinkingFor(model) : null;
  return JSON.stringify({
    systemInstruction: { parts: [{ text: system }] },
    contents,
    generationConfig: { temperature, maxOutputTokens: maxTokens, ...(thinking ? { thinkingConfig: thinking } : {}) },
  });
}

async function geminiGenerate(model, key, req, fetchImpl) {
  const url = `https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent?key=${encodeURIComponent(key)}`;
  const ms = req.timeoutMs || AI_TIMEOUT_MS;
  let r = await tfetch(fetchImpl, url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: geminiBody(req, model) }, ms);
  if (r.status === 400 && thinkingFor(model)) {
    // המודל לא מכיר את הגדרת החשיבה — שוב בלעדיה (ולא נופלים ל"נציג יחזור")
    r = await tfetch(fetchImpl, url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: geminiBody(req, model, false) }, ms);
  }
  if (!r.ok) throw new AiHttpError('gemini', r.status);
  const j = await r.json();
  const cand = j.candidates?.[0];
  let out = (cand?.content?.parts || []).filter((p) => !p.thought).map((p) => p.text || '').join('').trim();
  if (!out) throw new Error('gemini: empty answer');
  if (cand?.finishReason === 'MAX_TOKENS') out = trimCut(out);
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
  return geminiWithRetry({ key, model, req: { system, contents }, fetchImpl });
}

/** בקשה ל-Gemini (טקסט או אודיו) עם הניסיונות החוזרים ומודלי הגיבוי שלמעלה. */
async function geminiWithRetry({ key, model, req, fetchImpl = fetch }) {
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
      return await geminiGenerate(plan[i], key, req, fetchImpl);
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
  const body = JSON.stringify({ model: m, max_tokens: 1024, temperature: 0.6,
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
      const choice = (await r.json()).choices?.[0];
      let out = (choice?.message?.content || '').trim();
      if (!out) throw new Error('groq: empty answer');
      if (choice?.finish_reason === 'length') out = trimCut(out);
      return { text: out, model: m };
    } catch (e) {
      lastErr = e;
      if (!retryable(e)) throw e;
    }
  }
  throw lastErr;
}

// --- הודעות קוליות ------------------------------------------------------------------------------
// הלקוח שלח הודעה קולית → מתמללים לטקסט, והבוט עונה בכתב כרגיל (לא מחזירים הודעה קולית).
// אותו ספק ואותו מפתח שבדשבורד: Gemini מקבל את הקובץ עצמו (inlineData), ב-Groq — Whisper.
const VOICE_MAX_BYTES = 15 * 1024 * 1024;     // הודעה קולית של דקה ≈ 100KB; מעל זה — לא מתמללים
const TRANSCRIBE_TIMEOUT_MS = Number(process.env.FP_AI_TRANSCRIBE_MS) || 45000;
const NO_SPEECH = '[אין דיבור]';
const TRANSCRIBE_PROMPT = `תמלל/י את ההודעה הקולית מילה במילה, בשפה שבה דיברו (בדרך כלל עברית).
החזר/י רק את מה שנאמר — בלי הקדמה, בלי הסבר, בלי תרגום ובלי מירכאות.
אם אין בה דיבור ברור — החזר/י בדיוק: ${NO_SPEECH}`;
const WHISPER_EXT = { 'audio/ogg': 'ogg', 'audio/opus': 'opus', 'audio/mpeg': 'mp3', 'audio/mp3': 'mp3', 'audio/mp4': 'm4a',
  'audio/aac': 'm4a', 'audio/x-m4a': 'm4a', 'audio/wav': 'wav', 'audio/x-wav': 'wav', 'audio/webm': 'webm', 'audio/flac': 'flac' };

/** סוג הקובץ בלי פרמטרים: 'audio/ogg; codecs=opus' → 'audio/ogg'. */
function audioType(mimetype) {
  return String(mimetype || '').split(';')[0].trim().toLowerCase() || 'audio/ogg';
}

/** התמלול בלי מירכאות / הקדמה; "אין דיבור" → ''. */
function cleanTranscript(raw) {
  const t = String(raw || '').trim().replace(/^(תמלול|transcript(ion)?)\s*:\s*/i, '')
    .replace(/^["'״“”„]+|["'״“”„]+$/g, '').trim();
  return !t || t.includes(NO_SPEECH) ? '' : t;
}

async function groqWhisper(key, data, type, fetchImpl) {
  let lastErr = null;
  for (let i = 0; i < 2; i++) {
    if (i > 0) await sleep(RETRY_MS);
    try {
      const form = new FormData();
      form.append('file', new Blob([Buffer.from(data, 'base64')], { type }), `voice.${WHISPER_EXT[type] || 'ogg'}`);
      form.append('model', 'whisper-large-v3');
      form.append('prompt', 'שיחת וואטסאפ עם שירות הלקוחות של FilterPhone.');   // מטה לעברית בלי לכפות שפה
      form.append('response_format', 'json');
      form.append('temperature', '0');
      const r = await tfetch(fetchImpl, 'https://api.groq.com/openai/v1/audio/transcriptions', {
        method: 'POST', headers: { Authorization: `Bearer ${key}` }, body: form,
      }, TRANSCRIBE_TIMEOUT_MS);
      if (!r.ok) throw new AiHttpError('groq whisper', r.status);
      return String((await r.json()).text || '');
    } catch (e) {
      lastErr = e;
      if (!retryable(e)) throw e;
    }
  }
  throw lastErr;
}

/**
 * תמלול הודעה קולית ({data: base64, mimetype} — כמו msg.downloadMedia()). '' כשאין מפתח / אין דיבור.
 * תקלה (רשת / מכסה / קובץ גדול מדי) — נזרקת; הבוט מבקש מהלקוח לכתוב במילים.
 */
async function transcribe(cfg, media, fetchImpl = fetch) {
  const data = String(media?.data || '');
  if (!cfg?.ai_key || !data) return '';
  if (data.length * 0.75 > VOICE_MAX_BYTES) throw new Error('voice: too long to transcribe');
  const type = audioType(media.mimetype);
  if (cfg.ai_provider === 'gemini') {
    const req = { system: 'את/ה מתמלל/ת הודעות קוליות לטקסט.', temperature: 0, maxTokens: 8192, timeoutMs: TRANSCRIBE_TIMEOUT_MS,
      contents: [{ role: 'user', parts: [{ inlineData: { mimeType: type, data } }, { text: TRANSCRIBE_PROMPT }] }] };
    return cleanTranscript((await geminiWithRetry({ key: cfg.ai_key, model: cfg.ai_model, req, fetchImpl })).text);
  }
  if (cfg.ai_provider === 'groq') return cleanTranscript(await groqWhisper(cfg.ai_key, data, type, fetchImpl));
  return '';
}

// --- תמונות ------------------------------------------------------------------------------------
// הלקוח שלח תמונה (בדרך כלל צילום מסך של הטלפון) → תיאור קצר בטקסט, והבוט עונה לפיו כרגיל.
// בלי תיאור של אנשים — רק התוכן הטכני והטקסט שעל המסך.
const IMAGE_MAX_BYTES = 10 * 1024 * 1024;
const IMAGE_TYPES = new Set(['image/jpeg', 'image/png', 'image/webp', 'image/heic', 'image/heif']);
const GROQ_VISION = 'meta-llama/llama-4-scout-17b-16e-instruct';
const DESCRIBE_PROMPT = `את/ה עוזר/ת לנציג שירות של חברת סינון תוכן לטלפונים. לקוח שלח את התמונה הזו.
תאר/י בעברית, בקצרה ובדיוק, מה רואים בה — בעיקר את התוכן הטכני: איזו אפליקציה / מסך / הגדרה, ואת כל הטקסט שמופיע
(הודעות שגיאה, כפתורים, מספרים, שמות) מילה במילה.
אם יש בתמונה אנשים — אל תתאר/י את המראה שלהם; כתוב/כתבי רק "יש בתמונה אנשים".
החזר/י רק את התיאור, בלי הקדמה.`;

/** התיאור בלי הקדמה ("תיאור:"). מירכאות נשארות — בתוכן יש לעתים טקסט מצוטט מהמסך. */
function cleanDescription(raw) {
  return String(raw || '').trim().replace(/^(תיאור( התמונה)?|description)\s*:\s*/i, '').trim();
}

/** האם זה סוג קובץ שאפשר להבין: תמונה, וב-Gemini גם PDF. */
function describable(mimetype, provider) {
  const t = audioType(mimetype);
  return IMAGE_TYPES.has(t) || (provider === 'gemini' && t === 'application/pdf');
}

/**
 * תיאור תמונה / צילום מסך ששלח לקוח ({data: base64, mimetype} — כמו msg.downloadMedia()).
 * '' כשאין מפתח / סוג שלא נתמך. תקלה (רשת / מכסה) — נזרקת; הבוט מבקש מהלקוח לתאר במילים.
 */
async function describeImage(cfg, media, fetchImpl = fetch) {
  const data = String(media?.data || '');
  if (!cfg?.ai_key || !data || !describable(media.mimetype, cfg.ai_provider)) return '';
  if (data.length * 0.75 > IMAGE_MAX_BYTES) throw new Error('image: too big');
  const type = audioType(media.mimetype);
  if (cfg.ai_provider === 'gemini') {
    const req = { system: 'את/ה מתאר/ת תמונות ששלחו לקוחות לשירות לקוחות.', temperature: 0.2, maxTokens: 1024,
      timeoutMs: TRANSCRIBE_TIMEOUT_MS,
      contents: [{ role: 'user', parts: [{ inlineData: { mimeType: type, data } }, { text: DESCRIBE_PROMPT }] }] };
    return cleanDescription((await geminiWithRetry({ key: cfg.ai_key, model: cfg.ai_model, req, fetchImpl })).text);
  }
  if (cfg.ai_provider === 'groq') {
    const r = await tfetch(fetchImpl, 'https://api.groq.com/openai/v1/chat/completions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${cfg.ai_key}` },
      body: JSON.stringify({ model: GROQ_VISION, max_tokens: 700, temperature: 0.2, messages: [{ role: 'user', content: [
        { type: 'text', text: DESCRIBE_PROMPT },
        { type: 'image_url', image_url: { url: `data:${type};base64,${data}` } }] }] }),
    }, TRANSCRIBE_TIMEOUT_MS);
    if (!r.ok) throw new AiHttpError('groq vision', r.status);
    return cleanDescription((await r.json()).choices?.[0]?.message?.content || '');
  }
  return '';
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
    // גם מחוץ לשעות — לא "נחזור בשעות הפעילות": הבוט ינסה שוב בהודעה הבאה, ובעל העסק מקבל את השאלה
    const text = first ? 'שלום! תודה שפנית ל-FilterPhone 🙏 אני בודק ואחזור אליך בהקדם.' : 'קיבלתי 🙏 בודק ואחזור אליך בהקדם.';
    return { text, tags: [{ kind: 'ASK', arg: 'ה-AI לא היה זמין לרגע — אפשר לענות ללקוח כאן' }], ai: 'none' };
  }
  const text = inHours
    ? (first ? 'שלום! תודה שפנית ל-FilterPhone 🙏 נציג יחזור אליך בהקדם.' : 'קיבלתי, נציג יחזור אליך בהקדם 🙏')
    : `שלום! כעת מחוץ לשעות הפעילות (${hoursText}). נציג יחזור אליך בהקדם 🙏`;
  return { text, tags: [{ kind: 'HUMAN', arg: '' }], ai: 'none' };
}

module.exports = { systemPrompt, otherChannelSection, parseTags, addressBlock, pickGemini, geminiFallbacks, askGemini, askGroq, answer, fallback,
  thinkingFor, trimCut, transcribe, audioType, cleanTranscript, describeImage, describable,
  _resetModelCache: () => { modelCache = { key: '', names: [], at: 0 }; } };
