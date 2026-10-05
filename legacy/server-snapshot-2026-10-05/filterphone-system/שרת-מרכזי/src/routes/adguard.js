// ===================================
// נתיבי AdGuard Home API - FilterPhone
// מתחבר ל-AdGuard Home על http://localhost:3002
// POST /api/adguard/block-domain    - חסימת דומיין (מנהל)
// POST /api/adguard/unblock-domain  - ביטול חסימה (מנהל)
// GET  /api/adguard/stats           - סטטיסטיקות (מנהל)
// GET  /api/adguard/blocked-list    - רשימת חסימות (מנהל)
// ===================================
const express = require('express');
const router = express.Router();
const http = require('http');

const { authenticateToken, requireAdmin } = require('../middleware/auth');

// כתובת ופרטי גישה ל-AdGuard Home
const AGH_HOST = process.env.AGH_HOST || 'localhost';
const AGH_PORT = process.env.AGH_PORT || 3002;
const AGH_USER = process.env.AGH_USER || 'admin';
const AGH_PASS = process.env.AGH_PASS || 'REDACTED';
const AGH_AUTH = Buffer.from(`${AGH_USER}:${AGH_PASS}`).toString('base64');

// פונקציה עזר - בקשה ל-AdGuard Home API
function aghRequest(method, path, body = null) {
  return new Promise((resolve, reject) => {
    const options = {
      hostname: AGH_HOST,
      port: AGH_PORT,
      path: `/control${path}`,
      method,
      headers: {
        'Authorization': `Basic ${AGH_AUTH}`,
        'Content-Type': 'application/json',
      },
      timeout: 8000,
    };

    const req = http.request(options, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          try {
            resolve(data ? JSON.parse(data) : {});
          } catch {
            resolve(data);
          }
        } else {
          reject(new Error(`AdGuard API שגיאה ${res.statusCode}: ${data}`));
        }
      });
    });

    req.on('error', (err) => {
      reject(new Error(`לא ניתן להתחבר ל-AdGuard Home: ${err.message}`));
    });

    req.on('timeout', () => {
      req.destroy();
      reject(new Error('פסק זמן בחיבור ל-AdGuard Home'));
    });

    if (body) {
      req.write(JSON.stringify(body));
    }

    req.end();
  });
}

// ----- POST /api/adguard/block-domain -----
// חסימת דומיין ספציפי
router.post('/block-domain', authenticateToken, requireAdmin, async (req, res) => {
  const { domain } = req.body;

  if (!domain || typeof domain !== 'string') {
    return res.status(400).json({ error: 'נדרש שדה domain' });
  }

  // ניקוי ווידוא הדומיין
  const cleanDomain = domain.trim().toLowerCase().replace(/^https?:\/\//, '').replace(/\/.*$/, '');
  if (!/^[a-z0-9][a-z0-9\-\.]*\.[a-z]{2,}$/.test(cleanDomain)) {
    return res.status(400).json({ error: 'שם דומיין לא תקין. דוגמה: tiktok.com' });
  }

  try {
    // AdGuard Home API - הוספת חוק חסימה
    await aghRequest('POST', '/filtering/set_rules', {
      rules: [`||${cleanDomain}^`],
    });

    // לחסום גם את ה-www
    await aghRequest('POST', '/filtering/set_rules', {
      rules: [`||www.${cleanDomain}^`],
    });

    res.json({
      message: `הדומיין ${cleanDomain} נחסם בהצלחה`,
      domain: cleanDomain,
    });

  } catch (err) {
    // אם AdGuard לא זמין, עדיין מחזירים הצלחה חלקית
    console.warn('[AdGuard] שגיאה בחסימת דומיין:', err.message);
    res.status(503).json({ error: `שגיאה בחיבור ל-AdGuard Home: ${err.message}` });
  }
});

// ----- POST /api/adguard/unblock-domain -----
// ביטול חסימת דומיין
router.post('/unblock-domain', authenticateToken, requireAdmin, async (req, res) => {
  const { domain } = req.body;

  if (!domain) {
    return res.status(400).json({ error: 'נדרש שדה domain' });
  }

  const cleanDomain = domain.trim().toLowerCase().replace(/^https?:\/\//, '').replace(/\/.*$/, '');

  try {
    // קריאת החוקים הנוכחיים
    const currentRules = await aghRequest('GET', '/filtering/get_rules');
    const rules = (currentRules.rules || '').split('\n').filter(Boolean);

    // הסרת החוקים הקשורים לדומיין
    const filteredRules = rules.filter(rule =>
      !rule.includes(cleanDomain)
    );

    // עדכון החוקים
    await aghRequest('POST', '/filtering/set_rules', {
      rules: filteredRules,
    });

    res.json({
      message: `חסימת הדומיין ${cleanDomain} בוטלה`,
      domain: cleanDomain,
    });

  } catch (err) {
    console.warn('[AdGuard] שגיאה בביטול חסימה:', err.message);
    res.status(503).json({ error: `שגיאה בחיבור ל-AdGuard Home: ${err.message}` });
  }
});

// ----- GET /api/adguard/stats -----
// סטטיסטיקות AdGuard Home
router.get('/stats', authenticateToken, requireAdmin, async (req, res) => {
  try {
    const [stats, status] = await Promise.all([
      aghRequest('GET', '/stats'),
      aghRequest('GET', '/status'),
    ]);

    res.json({
      dns_queries: stats.num_dns_queries || 0,
      blocked_today: stats.num_blocked_filtering || 0,
      blocked_percent: stats.num_dns_queries > 0
        ? ((stats.num_blocked_filtering / stats.num_dns_queries) * 100).toFixed(1)
        : '0',
      top_blocked: stats.top_blocked_domains || [],
      top_clients: stats.top_clients || [],
      protection_enabled: status.protection_enabled,
      version: status.version,
      running: status.running,
    });

  } catch (err) {
    console.warn('[AdGuard] שגיאה בקריאת סטטיסטיקות:', err.message);
    // מחזירים נתונים ריקים אם AdGuard לא זמין
    res.json({
      dns_queries: 0,
      blocked_today: 0,
      blocked_percent: '0',
      top_blocked: [],
      top_clients: [],
      protection_enabled: false,
      version: 'לא זמין',
      running: false,
      error: 'AdGuard Home לא זמין',
    });
  }
});

// ----- GET /api/adguard/blocked-list -----
// רשימת דומיינים חסומים ידנית (החוקים שהוגדרו על ידי המנהל)
router.get('/blocked-list', authenticateToken, requireAdmin, async (req, res) => {
  try {
    const data = await aghRequest('GET', '/filtering/get_rules');
    const rules = (data.rules || '').split('\n').filter(Boolean);

    // חילוץ דומיינים מהחוקים בפורמט ||domain.com^
    const domains = rules
      .filter(rule => rule.startsWith('||') && rule.endsWith('^'))
      .map(rule => rule.replace('||', '').replace('^', ''))
      .filter(d => !d.startsWith('www.')) // הסרת כפילויות www
      .sort();

    res.json({ domains, total: domains.length });

  } catch (err) {
    console.warn('[AdGuard] שגיאה בקריאת רשימת חסימות:', err.message);
    res.json({ domains: [], total: 0, error: 'AdGuard Home לא זמין' });
  }
});

module.exports = router;
