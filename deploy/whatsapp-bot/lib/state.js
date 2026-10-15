'use strict';
/**
 * זיכרון הבוט (נשמר לקובץ, שורד הפעלה מחדש): מצב כל שיחה, היסטוריה קצרה להקשר, והאם הבוט פעיל.
 *   pausedUntil — כתבת בעצמך ללקוח: הבוט שותק בשיחה עד אז.
 *   handedOff   — הבוט העביר אליך (לקוח ביקש נציג): לא עונה עד שתחזיר אותו (תפריט "בוט" → 5).
 */
const fs = require('fs');
const path = require('path');

const HISTORY = 16;
const ASK_TTL_MS = 7 * 24 * 3600e3;
const ASK_MAX = 200;

class State {
  constructor(file) {
    this.file = file;
    this.data = { botActive: true, chats: {} };
    try {
      this.data = { ...this.data, ...JSON.parse(fs.readFileSync(file, 'utf8')) };
    } catch { /* קובץ חדש */ }
    this._timer = null;
  }

  chat(id) {
    const c = this.data.chats[id] || (this.data.chats[id] = { phone: '', pausedUntil: 0, handedOff: false, history: [], lastAt: 0, replies: [] });
    c.replies = c.replies || [];
    return c;
  }

  remember(id, role, content) {
    const c = this.chat(id);
    c.history.push({ role, content: String(content).slice(0, 1500) });
    if (c.history.length > HISTORY) c.history.splice(0, c.history.length - HISTORY);
    c.lastAt = Date.now();
    this.save();
  }

  paused(id, now = Date.now()) { return (this.data.chats[id]?.pausedUntil || 0) > now; }

  pause(id, ms, now = Date.now()) { this.chat(id).pausedUntil = now + ms; this.save(); }

  resume(id) { const c = this.chat(id); c.pausedUntil = 0; c.handedOff = false; this.save(); }

  reset(id) { delete this.data.chats[id]; this.save(); }

  /** הגבלת קצב: לא יותר מ-[max] תשובות של הבוט בשיחה אחת בתוך [windowMs] (נגד לולאות עם בוטים אחרים). */
  allowReply(id, now = Date.now(), max = 6, windowMs = 10 * 60e3) {
    const c = this.chat(id);
    c.replies = c.replies.filter((t) => now - t < windowMs);
    if (c.replies.length >= max) return false;
    c.replies.push(now);
    return true;
  }

  /**
   * שאלה שהבוט שלח לך (לא ידע לענות ללקוח) — לפי מזהה ההודעה בצ'אט שלך, כדי ש"השב" עליה יחזור ללקוח.
   * נשמרות שבוע (עד 200).
   */
  addAsk(msgId, rec, now = Date.now()) {
    const asks = this.data.asks || (this.data.asks = {});
    for (const [k, a] of Object.entries(asks)) if (now - (a.at || 0) > ASK_TTL_MS) delete asks[k];
    asks[msgId] = { ...rec, at: now };
    const keys = Object.keys(asks);
    for (const k of keys.slice(0, Math.max(0, keys.length - ASK_MAX))) delete asks[k];
    this.save();
  }

  getAsk(msgId, now = Date.now()) {
    const a = this.data.asks?.[msgId];
    return a && now - (a.at || 0) <= ASK_TTL_MS ? a : null;
  }

  /** כבר שאלת עליו לאחרונה ועוד לא ענית? (לא שולחים לך שאלה חדשה על כל הודעה של אותו לקוח) */
  openAsk(chatId, withinMs, now = Date.now()) {
    return Object.values(this.data.asks || {}).find((a) => a.chatId === chatId && !a.answered && now - (a.at || 0) < withinMs) || null;
  }

  /** ענית ללקוח בעצמך — השאלות הפתוחות עליו נסגרות. */
  closeAsks(chatId) {
    for (const a of Object.values(this.data.asks || {})) if (a.chatId === chatId) a.answered = true;
    this.save();
  }

  findByPhone(phone) {
    const p = String(phone).replace(/\D/g, '');
    return Object.keys(this.data.chats).find((id) => this.data.chats[id].phone === p || id.startsWith(p + '@')) || null;
  }

  recent(n = 10) {
    return Object.entries(this.data.chats).sort((a, b) => b[1].lastAt - a[1].lastAt).slice(0, n);
  }

  save() {
    clearTimeout(this._timer);
    this._timer = setTimeout(() => this.flush(), 500);
  }

  flush() {
    try {
      fs.mkdirSync(path.dirname(this.file), { recursive: true });
      const tmp = `${this.file}.tmp`;
      fs.writeFileSync(tmp, JSON.stringify(this.data));
      fs.renameSync(tmp, this.file);
    } catch (e) {
      console.warn('state save failed:', e.message);
    }
  }
}

module.exports = { State };
