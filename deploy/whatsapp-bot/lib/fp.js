'use strict';
/** הלקוח לשרת FilterPhone (מקומי: http://127.0.0.1:8090), מאומת בטוקן הבוט. */

function client(api, token, fetchImpl = fetch) {
  async function call(method, path, body) {
    const res = await fetchImpl(`${api}${path}`, {
      method,
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json', Accept: 'application/json' },
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: AbortSignal.timeout(15000),
    });
    const text = await res.text();
    let data = {};
    try { data = text ? JSON.parse(text) : {}; } catch { /* לא JSON */ }
    if (!res.ok) {
      const err = new Error(data.error || `HTTP ${res.status}`);
      // need=parent_code (+ wrong / locked) — הלקוח צריך להקליד את קוד ההורים
      Object.assign(err, { status: res.status, need: data.need, wrong: data.wrong, locked: data.locked });
      throw err;
    }
    return data;
  }
  return {
    config: () => call('GET', '/v1/bot/config'),
    status: (s) => call('POST', '/v1/bot/status', s),
    outbox: (after) => call('GET', `/v1/bot/outbox?after=${after || 0}`),
    forwarded: (ids, waIds) => call('POST', '/v1/bot/forwarded', { ids, wa_ids: waIds }),
    reply: (deviceId, text) => call('POST', '/v1/bot/reply', { device_id: deviceId, text }),
    customer: (phone, hint = {}) => call('GET', `/v1/bot/customer?phone=${encodeURIComponent(phone)}` +
      (hint.device ? `&device=${encodeURIComponent(hint.device)}` : '') + (hint.aid ? `&aid=${encodeURIComponent(hint.aid)}` : '')),
    block: (deviceId, options, parentCode) => call('POST', '/v1/bot/block',
      { device_id: deviceId, options, ...(parentCode ? { parent_code: parentCode } : {}) }),
    open: (deviceId, item, minutes, parentCode) => call('POST', '/v1/bot/open',
      { device_id: deviceId, item, minutes, ...(parentCode ? { parent_code: parentCode } : {}) }),
    close: (deviceId, item) => call('POST', '/v1/bot/close', { device_id: deviceId, item }),
    escalate: (deviceId, note) => call('POST', '/v1/bot/escalate', { device_id: deviceId, note }),
    // מצב לימוד: מה שהבוט למד ממך (שאלות ותשובות, עובדות)
    knowledge: () => call('GET', '/v1/bot/knowledge'),
    learn: (item) => call('POST', '/v1/bot/knowledge', item),
    forget: (id) => call('DELETE', `/v1/bot/knowledge/${encodeURIComponent(id)}`),
    used: (ids) => call('POST', '/v1/bot/knowledge/used', { ids }),
  };
}

module.exports = { client };
