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
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    return data;
  }
  return {
    config: () => call('GET', '/v1/bot/config'),
    status: (s) => call('POST', '/v1/bot/status', s),
    outbox: (after) => call('GET', `/v1/bot/outbox?after=${after || 0}`),
    forwarded: (ids, waIds) => call('POST', '/v1/bot/forwarded', { ids, wa_ids: waIds }),
    reply: (deviceId, text) => call('POST', '/v1/bot/reply', { device_id: deviceId, text }),
    customer: (phone) => call('GET', `/v1/bot/customer?phone=${encodeURIComponent(phone)}`),
    block: (deviceId, options) => call('POST', '/v1/bot/block', { device_id: deviceId, options }),
    escalate: (deviceId, note) => call('POST', '/v1/bot/escalate', { device_id: deviceId, note }),
  };
}

module.exports = { client };
