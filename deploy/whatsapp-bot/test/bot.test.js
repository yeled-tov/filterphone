'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { isOpen, parseRanges } = require('../lib/hours');
const ai = require('../lib/ai');
const { State } = require('../lib/state');
const { replyDelayMs, typingMs, parseHashReply, waNumber } = require('../lib/logic');
const labels = require('../lib/labels');

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

test('AI failure or no key → polite fixed answer and hand off to a human', async () => {
  const boom = async () => ({ ok: false, status: 429, json: async () => ({}) });
  const r = await ai.answer({ ai_provider: 'gemini', ai_key: 'K', ai_model: 'gemini-2.5-flash' },
    { inHours: false, hoursText: HOURS, history: [], text: 'שלום' }, boom);
  assert.match(r.text, /מחוץ לשעות הפעילות/);
  assert.deepEqual(r.tags, [{ kind: 'HUMAN', arg: '' }]);
  assert.match(r.error, /429/);
  const n = await ai.answer({ ai_provider: 'none', ai_key: '' }, { inHours: true, hoursText: HOURS, history: [], text: 'x' });
  assert.equal(n.ai, 'none');
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
  assert.ok(typingMs('קצר') >= 1200 && typingMs('x'.repeat(5000)) === 6000);
  assert.deepEqual(parseHashReply('#42 תפעיל מחדש'), { deviceId: 42, text: 'תפעיל מחדש' });
  assert.equal(parseHashReply('שלום'), null);
  assert.equal(waNumber('052-718-6881'), '972527186881');
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
