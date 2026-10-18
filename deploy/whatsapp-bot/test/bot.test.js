'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { isOpen, parseRanges } = require('../lib/hours');
const ai = require('../lib/ai');
const { State } = require('../lib/state');
const { replyDelayMs, combineBurst, typingMs, parseHashReply, waNumber } = require('../lib/logic');
const labels = require('../lib/labels');
const aimark = require('../lib/aimark');
const acts = require('../lib/actions');

const HOURS = '13:00-15:00, 20:00-22:30';
const at = (day, hh, mm = 0) => ({ day, minutes: hh * 60 + mm });

test('business hours like the old bot: ranges, Friday until noon, Shabbat closed', () => {
  assert.deepEqual(parseRanges(HOURS), [[780, 900], [1200, 1350]]);
  assert.equal(isOpen(at(0, 13, 30), HOURS, '12:00'), true);
  assert.equal(isOpen(at(0, 16, 0), HOURS, '12:00'), false);
  assert.equal(isOpen(at(3, 22, 30), HOURS, '12:00'), true);
  assert.equal(isOpen(at(5, 13, 30), HOURS, '12:00'), false);       // שישי אחרי 12:00
  assert.equal(isOpen(at(6, 13, 30), HOURS, '12:00'), false);       // שבת
  assert.equal(isOpen(at(1, 13, 30), '', '12:00'), false);
});

test('tags are separated from the customer text (new and old style)', () => {
  assert.deepEqual(ai.parseTags('אשמח לעזור!\n[[ADDRESS]]'), { text: 'אשמח לעזור!', tags: [{ kind: 'ADDRESS', arg: '' }] });
  const r = ai.parseTags('חסמתי לך 🙂\n[[BLOCK:tiktok]]\n[[ESCALATE:להשהות ליום]]');
  assert.equal(r.text, 'חסמתי לך 🙂');
  assert.deepEqual(r.tags, [{ kind: 'BLOCK', arg: 'tiktok' }, { kind: 'ESCALATE', arg: 'להשהות ליום' }]);
  assert.deepEqual(ai.parseTags('נציג יחזור אליך HANDOFF').tags, [{ kind: 'HUMAN', arg: '' }]);
});

test('address block comes from the business info with navigation links', () => {
  const b = ai.addressBlock('שלום\nכתובת: חטיבת גבעת שתיים, אשדוד.\nעוד');
  assert.match(b, /חטיבת גבעת שתיים, אשדוד/);
  assert.match(b, /waze\.com\/ul\?q=/);
  assert.equal(ai.addressBlock('בלי כתובת'), '');
});

test('the prompt carries the business info, hours and CRM context', () => {
  const p = ai.systemPrompt({ botName: 'פילטר', businessInfo: 'כשר פליי — צריבה 70₪', inHours: false,
    hoursText: HOURS, crm: { customer: { name: 'משה' }, devices: [{}] } });
  assert.match(p, /כשר פליי — צריבה 70₪/);
  assert.match(p, /מחוץ לשעות הפעילות/);
  assert.match(p, /משה, 1 מכשירים/);
  assert.match(p, /\[\[HUMAN\]\]/);
});

test('gemini: picks the newest flash model and sends system + history', async () => {
  ai._resetModelCache();
  const calls = [];
  const fakeFetch = async (url, opts = {}) => {
    calls.push({ url, body: opts.body ? JSON.parse(opts.body) : null });
    if (url.includes('/models?')) {
      return { ok: true, json: async () => ({ models: [
        { name: 'models/gemini-2.0-flash', supportedGenerationMethods: ['generateContent'] },
        { name: 'models/gemini-2.5-flash', supportedGenerationMethods: ['generateContent'] },
        { name: 'models/embedding-001', supportedGenerationMethods: ['embedContent'] }] }) };
    }
    return { ok: true, json: async () => ({ candidates: [{ content: { parts: [{ text: 'שלום! במה אפשר לעזור?' }] } }] }) };
  };
  const res = await ai.answer({ ai_provider: 'gemini', ai_key: 'K', ai_model: '' },
    { botName: 'פילטר', businessInfo: 'מידע', inHours: true, hoursText: HOURS, crm: null,
      history: [{ role: 'user', content: 'היי' }, { role: 'assistant', content: 'שלום' }], text: 'כמה עולה?' }, fakeFetch);
  assert.equal(res.text, 'שלום! במה אפשר לעזור?');
  assert.equal(res.ai, 'gemini');
  const gen = calls.find((c) => c.url.includes(':generateContent'));
  assert.match(gen.url, /gemini-2\.5-flash:generateContent/);
  assert.match(gen.body.systemInstruction.parts[0].text, /מידע/);
  assert.deepEqual(gen.body.contents.map((c) => c.role), ['user', 'model', 'user']);
  assert.equal(gen.body.contents[2].parts[0].text, 'כמה עולה?');
});

test('AI outage → polite answer and a question to the owner (bot stays in the chat); no key → human', async () => {
  ai._resetModelCache();
  const boom = async () => ({ ok: false, status: 429, json: async () => ({}) });
  const r = await ai.answer({ ai_provider: 'gemini', ai_key: 'K', ai_model: 'gemini-2.5-flash' },
    { inHours: false, hoursText: HOURS, history: [], text: 'שלום' }, boom);
  assert.match(r.text, /מחוץ לשעות הפעילות/);
  assert.equal(r.tags.length, 1);
  assert.equal(r.tags[0].kind, 'ASK');                     // לא HUMAN — לא מוציאים את הבוט מהשיחה
  assert.match(r.error, /429/);
  const n = await ai.answer({ ai_provider: 'none', ai_key: '' }, { inHours: true, hoursText: HOURS, history: [], text: 'x' });
  assert.equal(n.ai, 'none');
  assert.deepEqual(n.tags, [{ kind: 'HUMAN', arg: '' }]);
});

test('gemini 503 (busy) → tries again, then a fallback model, and answers', async () => {
  ai._resetModelCache();
  const gens = [];
  const fakeFetch = async (url) => {
    if (url.includes('/models?')) {
      return { ok: true, json: async () => ({ models: [
        { name: 'models/gemini-2.5-flash', supportedGenerationMethods: ['generateContent'] },
        { name: 'models/gemini-2.0-flash', supportedGenerationMethods: ['generateContent'] },
        { name: 'models/gemini-2.5-flash-lite', supportedGenerationMethods: ['generateContent'] }] }) };
    }
    const model = /models\/([^:]+):generateContent/.exec(url)[1];
    gens.push(model);
    if (model === 'gemini-2.5-flash') return { ok: false, status: 503, json: async () => ({}) };
    return { ok: true, json: async () => ({ candidates: [{ content: { parts: [{ text: 'כן, אפשר לחסום טיקטוק 👍' }] } }] }) };
  };
  const res = await ai.answer({ ai_provider: 'gemini', ai_key: 'K', ai_model: '' },
    { inHours: true, hoursText: HOURS, history: [], text: 'אפשר לחסום טיקטוק?' }, fakeFetch);
  assert.equal(res.text, 'כן, אפשר לחסום טיקטוק 👍');
  assert.equal(res.ai, 'gemini');
  assert.equal(res.error, undefined);
  assert.deepEqual(gens, ['gemini-2.5-flash', 'gemini-2.5-flash', 'gemini-2.0-flash']);
});

test('gemini: room for the whole answer, no thinking on fast models; a cut answer ends at a full sentence', async () => {
  ai._resetModelCache();
  const bodies = [];
  const fakeFetch = async (url, opts = {}) => {
    bodies.push(JSON.parse(opts.body));
    return { ok: true, json: async () => ({ candidates: [{ finishReason: 'MAX_TOKENS', content: { parts: [
      { text: 'חושב…', thought: true },
      { text: 'שלום! קיבלתי את הפרטים. במה אוכל לעזור לך במכש' }] } }] }) };
  };
  const res = await ai.answer({ ai_provider: 'gemini', ai_key: 'K', ai_model: 'gemini-2.5-flash' },
    { inHours: true, hoursText: HOURS, history: [], text: 'שלום' }, fakeFetch);
  assert.equal(res.text, 'שלום! קיבלתי את הפרטים.');            // בלי חצי מילה, ובלי טקסט ה"חשיבה"
  assert.ok(bodies[0].generationConfig.maxOutputTokens >= 2048);
  assert.deepEqual(bodies[0].generationConfig.thinkingConfig, { thinkingBudget: 0 });
  assert.deepEqual(ai.thinkingFor('gemini-3-flash'), { thinkingLevel: 'minimal' });
  assert.equal(ai.thinkingFor('gemini-2.5-pro'), null);
});

test('gemini: a model that rejects the thinking setting is asked again without it', async () => {
  ai._resetModelCache();
  const bodies = [];
  const fakeFetch = async (url, opts = {}) => {
    const b = JSON.parse(opts.body);
    bodies.push(b);
    if (b.generationConfig.thinkingConfig) return { ok: false, status: 400, json: async () => ({}) };
    return { ok: true, json: async () => ({ candidates: [{ content: { parts: [{ text: 'בטח, אפשר.' }] } }] }) };
  };
  const res = await ai.answer({ ai_provider: 'gemini', ai_key: 'K', ai_model: 'gemini-3-flash' },
    { inHours: true, hoursText: HOURS, history: [], text: 'אפשר?' }, fakeFetch);
  assert.equal(res.text, 'בטח, אפשר.');
  assert.equal(bodies.length, 2);
});

test('gemini: a key/permission error is not retried', async () => {
  ai._resetModelCache();
  let calls = 0;
  const denied = async () => { calls += 1; return { ok: false, status: 403, json: async () => ({}) }; };
  const r = await ai.answer({ ai_provider: 'gemini', ai_key: 'BAD', ai_model: 'gemini-2.5-flash' },
    { inHours: true, hoursText: HOURS, history: [], text: 'x' }, denied);
  assert.match(r.error, /403/);
  assert.equal(calls, 1);
});

test('fallback models: stable flash newest first, then flash-lite', () => {
  assert.deepEqual(ai.geminiFallbacks(['gemini-2.0-flash', 'gemini-2.5-flash-lite', 'gemini-2.5-flash',
    'gemini-2.5-pro', 'gemini-3.0-flash-preview'], 'gemini-2.5-flash'), ['gemini-2.0-flash', 'gemini-2.5-flash-lite']);
});

test('state: handoff pause, rate limit, persistence and lookup by phone', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'wabot-'));
  const file = path.join(dir, 'state.json');
  const s = new State(file);
  s.chat('972501234567@c.us').phone = '972501234567';
  s.pause('972501234567@c.us', 60e3, 1000);
  assert.equal(s.paused('972501234567@c.us', 2000), true);
  assert.equal(s.paused('972501234567@c.us', 1000 + 60e3 + 1), false);
  for (let i = 0; i < 6; i++) assert.equal(s.allowReply('x@c.us', 10_000 + i), true);
  assert.equal(s.allowReply('x@c.us', 10_010), false);                 // שביעית תוך 10 דקות
  assert.equal(s.allowReply('x@c.us', 10_000 + 11 * 60e3), true);
  s.remember('972501234567@c.us', 'user', 'שלום');
  s.flush();
  const again = new State(file);
  assert.equal(again.findByPhone('0501234567'.replace(/^0/, '972')), '972501234567@c.us');
  assert.equal(again.chat('972501234567@c.us').history[0].content, 'שלום');
});

test('small decisions: reply delay, typing time, #reply, phone format', () => {
  assert.equal(replyDelayMs({ inHours: false, adminActive: true, adminRead: true }), 0);
  assert.equal(replyDelayMs({ inHours: true, adminActive: true }), 5000);
  assert.equal(replyDelayMs({ inHours: true, adminActive: false, adminRead: false }), 0);
  // חלון שקט: תמיד מחכים שהלקוח יסיים לכתוב — לפחות 7 שניות, וגם לא פחות מההמתנה למנהל
  assert.ok(typingMs('קצר') >= 1200 && typingMs('x'.repeat(5000)) === 6000);
  assert.deepEqual(parseHashReply('#42 תפעיל מחדש'), { deviceId: 42, text: 'תפעיל מחדש' });
  assert.equal(parseHashReply('שלום'), null);
  assert.equal(waNumber('052-718-6881'), '972527186881');
  // כמה הודעות רצופות = שאלה אחת (בלי כפילות עוקבת ובלי ריקות)
  assert.equal(combineBurst(['שלום', 'אני רוצה', 'לשנות רמה']), 'שלום\nאני רוצה\nלשנות רמה');
  assert.equal(combineBurst(['היי', 'היי', '  ', 'עוד']), 'היי\nעוד');
  assert.equal(combineBurst([]), '');
});

test('the "בוט" list: found by name, other lists kept, new customer vs. your own chat', () => {
  const all = [{ id: '1', name: 'לקוח חדש' }, { id: '5', name: ' בוט\u200f ' }, { id: '7', name: 'שולם' }];
  assert.equal(labels.findLabel(all, 'בוט').id, '5');
  assert.equal(labels.findLabel(all, '').id, '5');                          // ברירת מחדל: "בוט"
  assert.equal(labels.findLabel(all, 'רובוט'), null);
  assert.deepEqual(labels.withLabel(['1', '7'], '5', true), ['1', '7', '5']);
  assert.deepEqual(labels.withLabel(['1', '5', '7'], 5, false), ['1', '7']);
  assert.deepEqual(labels.withLabel([], '5', false), []);

  const incoming = [{ fromMe: false }, { fromMe: false }];
  assert.equal(labels.isNewChat(incoming), true);                           // מעולם לא כתבת לו
  assert.equal(labels.isNewChat([...incoming, { fromMe: true }]), false);   // שיחה שלך
  assert.equal(labels.isNewChat(incoming, { isContact: true }), false);     // איש קשר שמור
  assert.equal(labels.isNewChat(incoming, { isContact: true, includeContacts: true }), true);
});

test('bot put back in a chat: answers only a recent, unanswered customer message', () => {
  const now = 1_800_000_000_000;
  const msg = (o) => ({ id: { _serialized: 'm1' }, fromMe: false, body: 'שלום', timestamp: now / 1000 - 60, ...o });
  assert.equal(labels.needsCatchUp(msg({}), { now }), true);
  assert.equal(labels.needsCatchUp(msg({}), { now, answered: 'm1' }), false);              // כבר ענינו
  assert.equal(labels.needsCatchUp(msg({ fromMe: true }), { now }), false);                // אתה ענית
  assert.equal(labels.needsCatchUp(msg({ body: '' }), { now }), false);                    // לא טקסט
  assert.equal(labels.needsCatchUp(msg({ timestamp: now / 1000 - 3600 }), { now }), false); // ישן
  assert.equal(labels.needsCatchUp(null, { now }), false);
});

test('library patch: transient page errors are retried, real errors are not', async () => {
  const { withRetry, isTransient } = require('../lib/wwebjs_patch');
  const noSleep = { sleep: async () => {} };
  assert.equal(isTransient(new Error('Protocol error (Runtime.addBinding): Target closed')), true);
  assert.equal(isTransient(new Error('Execution context was destroyed, most likely because of a navigation')), true);
  assert.equal(isTransient(new Error('Cannot read properties of undefined')), false);
  let n = 0;
  assert.equal(await withRetry(async () => { n += 1; if (n < 3) throw new Error('Target closed'); return 'ok'; }, noSleep), 'ok');
  assert.equal(n, 3);
  n = 0;
  await assert.rejects(withRetry(async () => { n += 1; throw new Error('boom'); }, noSleep), /boom/);
  assert.equal(n, 1);                                                // לא חוזרים על שגיאה אמיתית
  n = 0;
  await assert.rejects(withRetry(async () => { n += 1; throw new Error('Target closed'); }, { ...noSleep, attempts: 4 }));
  assert.equal(n, 4);
});

test('library patch applies to whatsapp-web.js before it loads (when installed)', () => {
  let installed = true;
  try { require.resolve('whatsapp-web.js/src/util/Puppeteer'); } catch { installed = false; }
  const { apply } = require('../lib/wwebjs_patch');
  assert.equal(apply(), installed);
});

test('learning: relevant past answers are picked, facts always, nothing personal', () => {
  const know = require('../lib/knowledge');
  const items = [
    { id: 1, kind: 'qa', question: 'כמה עולה סינון למכשיר?', answer: '250 ₪ כולל התקנה', updated_at: 10 },
    { id: 2, kind: 'qa', question: 'אתם פתוחים בשישי?', answer: 'עד 12:00', updated_at: 20 },
    { id: 3, kind: 'fact', question: '', answer: 'משלוח לכל הארץ תוך 3 ימי עסקים', updated_at: 5 },
  ];
  const sel = know.select(items, 'שלום, כמה עולה הסינון?');
  assert.deepEqual(sel.qas.map((q) => q.id), [1]);
  assert.deepEqual(sel.facts.map((f) => f.id), [3]);
  assert.deepEqual(sel.ids.sort(), [1, 3]);
  const section = know.promptSection(sel);
  assert.match(section, /250 ₪/);
  assert.match(section, /משלוח לכל הארץ/);
  assert.doesNotMatch(section, /עד 12:00/);
  assert.equal(know.promptSection({ facts: [], qas: [] }), '');
  // הסינון של מה שנלמד מתשובה שלך בשיחה
  assert.equal(know.worthLearning('אוקיי', 'כמה עולה סינון?'), false);
  assert.equal(know.worthLearning('רגע, בודק', 'כמה עולה סינון?'), false);
  assert.equal(know.worthLearning('תתקשר ל-050-1234567', 'כמה עולה סינון?'), false);
  assert.equal(know.worthLearning('250 ₪ כולל התקנה', 'שלום'), false);          // "שלום" זו לא שאלה
  assert.equal(know.worthLearning('250 ₪ כולל התקנה', 'כמה עולה סינון?'), true);
  assert.equal(know.hasPhone('+972 52 123 4567'), false);                         // רק תבנית רגילה
  assert.equal(know.hasPhone('0521234567'), true);
});

test('learning: teach / forget / list commands from your own chat', () => {
  const know = require('../lib/knowledge');
  assert.deepEqual(know.parseCommand('למד: משלוח תוך 3 ימים'), { cmd: 'teach', kind: 'fact', answer: 'משלוח תוך 3 ימים' });
  assert.deepEqual(know.parseCommand('למד:משלוח תוך 3 ימים'), { cmd: 'teach', kind: 'fact', answer: 'משלוח תוך 3 ימים' });
  assert.deepEqual(know.parseCommand('ש: כמה עולה?\nת: 250 ₪'), { cmd: 'teach', kind: 'qa', question: 'כמה עולה?', answer: '250 ₪' });
  assert.deepEqual(know.parseCommand('שכח 12'), { cmd: 'forget', id: 12 });
  assert.deepEqual(know.parseCommand('מה למדת?'), { cmd: 'list' });
  assert.equal(know.parseCommand('למדתי משהו'), null);
  assert.equal(know.parseCommand('3'), null);                                     // התפריט
  assert.match(know.listText([]), /עוד לא למדתי/);
  const list = know.listText([{ id: 7, kind: 'qa', question: 'כמה עולה?', answer: '250' }, { id: 8, kind: 'fact', answer: 'סגור בשבת' }]);
  assert.match(list, /#7/);
  assert.match(list, /סגור בשבת/);
  assert.match(list, /שכח 7/);
});

test('learning: the AI gets what it learned and may ask you instead of guessing', () => {
  const p = ai.systemPrompt({ botName: 'פילטר', businessInfo: 'מחיר: 100', inHours: true, hoursText: '', crm: null,
    knowledge: '=== מה שלמדת ===\nש: x\nת: y', askWhenUnsure: true });
  assert.match(p, /מה שלמדת/);
  assert.match(p, /\[\[ASK:/);
  assert.doesNotMatch(ai.systemPrompt({ businessInfo: '', inHours: true }), /\[\[ASK:/);
  const r = ai.parseTags('אני בודק ואחזור אליך 🙏\n[[ASK:האם יש מלאי של אייפון 13]]');
  assert.equal(r.text, 'אני בודק ואחזור אליך 🙏');
  assert.deepEqual(r.tags, [{ kind: 'ASK', arg: 'האם יש מלאי של אייפון 13' }]);
});

test('learning: questions the bot asked you survive a restart and close when you answer', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'wabot-'));
  const file = path.join(dir, 'state.json');
  const s = new State(file);
  s.addAsk('msg1', { chatId: 'c1@c.us', phone: '9725', question: 'יש מלאי?', missing: 'מלאי' }, 1000);
  assert.ok(s.openAsk('c1@c.us', 60e3, 2000));
  assert.equal(s.openAsk('c2@c.us', 60e3, 2000), null);
  s.flush();
  const s2 = new State(file);
  assert.equal(s2.getAsk('msg1', 2000).question, 'יש מלאי?');
  s2.closeAsks('c1@c.us');
  assert.equal(s2.openAsk('c1@c.us', 60e3, 2000), null);
  assert.equal(s2.getAsk('msg1', 1000 + 8 * 24 * 3600e3), null);                  // אחרי שבוע — נשכח
  clearTimeout(s._timer);
  clearTimeout(s2._timer);
});

test('AI mark: the WhatsApp label unless turned off or rejected this week; text tag only as a fallback', () => {
  const now = Date.now();
  assert.equal(aimark.nativeOn({ mark_ai: true }, 0, now), true);
  assert.equal(aimark.nativeOn({}, 0, now), true);                               // ברירת מחדל: פעיל
  assert.equal(aimark.nativeOn({ mark_ai: false }, 0, now), false);
  assert.equal(aimark.nativeOn({ mark_ai: true }, now - 3600e3, now), false);      // נדחה לפני שעה
  assert.equal(aimark.nativeOn({ mark_ai: true }, now - 8 * 24 * 3600e3, now), true);   // ניסיון חוזר אחרי שבוע
  assert.equal(aimark.withTextTag({ mark_ai: true, ai_tag: '🤖 אוטומטי' }, 'שלום'), 'שלום\n\n🤖 אוטומטי');
  assert.equal(aimark.withTextTag({ mark_ai: false, ai_tag: '🤖 אוטומטי' }, 'שלום'), 'שלום');
  assert.equal(aimark.withTextTag({ mark_ai: true, ai_tag: '' }, 'שלום'), 'שלום');
  assert.equal(aimark.BIZ_BOT_TYPE, 'biz_1p');
});

test('actions: device # and Android ID from the app message; minutes capped at an hour', () => {
  const msg = 'שלום, אני צריך עזרה ב-FilterPhone.\nמכשיר DUOQIN F22Pro · #16\nטלפון: 0527186881\nמזהה: 7d1ac306b6d02fe3\nVPN: מחובר';
  assert.deepEqual(acts.deviceHint(msg), { device: 16, aid: '7d1ac306b6d02fe3' });
  assert.deepEqual(acts.deviceHint('תפתח לי את החנות', { device: 16, aid: 'abc' }), { device: 16, aid: 'abc' });   // נשמר מקודם
  assert.deepEqual(acts.parseOpen('play_store:90'), { item: 'play_store', minutes: 60 });
  assert.deepEqual(acts.parseOpen('hotspot'), { item: 'hotspot', minutes: 10 });
  assert.deepEqual(acts.parseOpen('play_store:0'), { item: 'play_store', minutes: 10 });
});

test('actions: the AI gets a closed list per device, or is told how to identify the customer', () => {
  assert.match(acts.promptSection(null), /תמיכה בוואטסאפ/);
  const two = { selected: null, devices: [{ device_id: 3, name: 'A' }, { device_id: 4, name: 'B' }] };
  assert.match(acts.promptSection(two), /#3 A, #4 B/);
  const one = { selected: 16, devices: [{ device_id: 16, temp_open: [] }],
    actions: { open: [{ id: 'play_store', title: 'כניסה לחנות Google Play' }], block: [{ id: 'tiktok', title: 'טיקטוק' }],
      max_minutes: 60, default_minutes: 10 } };
  const p = acts.promptSection(one);
  assert.match(p, /\[\[OPEN:מזהה:דקות\]\]/);
  assert.match(p, /play_store = כניסה לחנות Google Play/);
  assert.match(p, /tiktok = טיקטוק/);
  assert.match(p, /לכל היותר 60/);
  const sys = ai.systemPrompt({ businessInfo: 'x', inHours: true, hoursText: HOURS, actions: p });
  assert.match(sys, /play_store = כניסה לחנות Google Play/);
  assert.deepEqual(ai.parseTags('פותח לך 👍\n[[OPEN:play_store:15]]').tags, [{ kind: 'OPEN', arg: 'play_store:15' }]);
});

test('actions: confirmation lines tell the customer exactly what happened', () => {
  const until = Math.floor(Date.UTC(2026, 9, 18, 11, 40) / 1000);   // 14:40 בישראל
  assert.match(acts.confirmLine('OPEN', { title: 'כניסה לחנות Google Play', until, minutes: 10 }), /פתוח עד 14:40 \(10 דק'\)/);
  assert.match(acts.confirmLine('OPEN', { title: 'נקודה חמה', already_open: true }), /כבר פתוח/);
  assert.match(acts.confirmLine('BLOCK', { titles: ['נקודה חמה (שיתוף אינטרנט)'] }), /נחסם: נקודה חמה/);
  assert.match(acts.confirmLine('BLOCK', { titles: [] }), /כבר חסום/);
  assert.equal(acts.confirmLine('CLOSE', { closed: false }), '');
});

// ------------------------------------------------------------------ קוד הורים

const pcode = require('../lib/parentcode');
const fp = require('../lib/fp');

test('parent code: only 4-8 digits are taken as the code, never other text', () => {
  assert.equal(pcode.extract('1234'), '1234');
  assert.equal(pcode.extract(' 12 34 '), '1234');
  assert.equal(pcode.extract('12-34-56'), '123456');
  assert.equal(pcode.extract('‏4321‎'), '4321');
  assert.equal(pcode.extract('123'), null);
  assert.equal(pcode.extract('123456789'), null);
  assert.equal(pcode.extract('הקוד הוא 1234'), null);
  assert.equal(pcode.extract('תפתח לי את החנות'), null);
});

test('parent code: the reply depends on why the server refused', () => {
  assert.equal(pcode.message([{ need: 'parent_code' }]), pcode.ASK);
  assert.equal(pcode.message([{ need: 'parent_code', wrong: true }]), pcode.WRONG);
  assert.equal(pcode.message([{ need: 'parent_code', wrong: true, locked: true }]), pcode.LOCKED);
  assert.equal(pcode.isCodeError({ need: 'parent_code' }), true);
  assert.equal(pcode.isCodeError(new Error('x')), false);
});

test('parent code: pending actions expire after 5 minutes', () => {
  let now = 0;
  const s = pcode.store(() => now);
  s.set('chat', { deviceId: 7, tags: [{ kind: 'OPEN', arg: 'store' }] });
  now = pcode.TTL_MS - 1;
  assert.equal(s.get('chat').deviceId, 7);
  assert.equal(s.take('chat').deviceId, 7);
  assert.equal(s.get('chat'), null);
  s.set('chat', { deviceId: 8 });
  now += pcode.TTL_MS + 1;
  assert.equal(s.get('chat'), null);
});

test('server client: the parent code goes in the body, and a refusal says why', async () => {
  const calls = [];
  const fake = async (url, init) => {
    calls.push(JSON.parse(init.body));
    return { ok: false, status: 403, text: async () => JSON.stringify({ error: 'קוד ההורים שגוי', need: 'parent_code', wrong: true }) };
  };
  const srv = fp.client('http://x', 't', fake);
  await assert.rejects(srv.open(5, 'store', 10, '1234'), (e) => e.need === 'parent_code' && e.wrong === true && e.status === 403);
  assert.deepEqual(calls[0], { device_id: 5, item: 'store', minutes: 10, parent_code: '1234' });
  await assert.rejects(srv.block(5, ['a']));
  assert.equal('parent_code' in calls[1], false);
});

test('the AI is told about the parent code but never asks for it itself', () => {
  const crm = { selected: 3, customer: { parent_code: true }, devices: [{ device_id: 3, temp_open: [] }],
    actions: { open: [], block: [], opened: [] } };
  const p = acts.promptSection(crm);
  assert.match(p, /קוד הורים/);
  assert.match(p, /לעולם אל תבקש/);
  assert.doesNotMatch(acts.promptSection({ ...crm, customer: { parent_code: false } }), /קוד הורים/);
});
