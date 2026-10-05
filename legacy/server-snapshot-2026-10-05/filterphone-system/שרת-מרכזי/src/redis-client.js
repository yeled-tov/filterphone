// ===================================
// לקוח Redis משותף — FilterPhone
// ===================================
const { createClient } = require('redis');

let client = null;
let connecting = false;

async function ensureConnected() {
  if (client && client.isOpen) return client;
  if (connecting) return null;

  connecting = true;
  try {
    client = createClient({ url: 'redis://127.0.0.1:6379' });
    client.on('error', (e) => console.warn('[Redis]', e.message));
    await client.connect();
    connecting = false;
    return client;
  } catch (e) {
    console.warn('[Redis] לא זמין:', e.message);
    connecting = false;
    return null;
  }
}

// התחברות מיידית
ensureConnected();

module.exports = {
  getClient: () => client && client.isOpen ? client : null,
  ensureConnected,

  // פונקציית עזר — סנכרון רמת סינון ל-Redis לפי VPN IP
  async syncFilterLevel(vpnIp, filterLevel) {
    try {
      const r = await ensureConnected();
      if (r && vpnIp) {
        await r.set(`filterphone:vpn_ip:${vpnIp}:filter_level`, String(filterLevel));
        console.log(`[Redis] סונכרנה רמת סינון ${filterLevel} ל-IP ${vpnIp}`);
        return true;
      }
    } catch (e) {
      console.warn('[Redis] שגיאה בסנכרון רמת סינון:', e.message);
    }
    return false;
  }
};
