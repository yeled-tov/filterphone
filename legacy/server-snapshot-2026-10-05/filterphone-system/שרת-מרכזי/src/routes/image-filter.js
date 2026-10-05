// ===================================
// נתיבי סינון תמונות - FilterPhone (גרסה חדשה)
// mitmproxy במצב regular proxy
// Redis לסטטיסטיקות ו-cache
// ===================================
const express = require('express');
const router = express.Router();
const { execSync, exec } = require('child_process');
const path = require('path');
const fs = require('fs');

const { authenticateToken, requireAdmin } = require('../middleware/auth');
const redisClient = require('../redis-client');

// ===== קבועים =====
const DOMAINS_PATH = path.join(__dirname, '../../../סינון-תמונות/browser_domains.txt');
const MITM_SERVICE = 'filterphone-mitm';

// ===== GET /api/image-filter/stats =====
// סטטיסטיקות סינון מ-Redis
router.get('/stats', authenticateToken, requireAdmin, async (req, res) => {
  try {
    const r = redisClient.getClient();
    if (!r) {
      return res.json({
        images_scanned: 0, images_blocked: 0, images_passed: 0,
        cache_hits: 0, block_rate: '0',
        error: 'Redis לא זמין',
      });
    }

    const scanned = parseInt(await r.get('filterphone:stats:images_scanned') || 0);
    const blocked = parseInt(await r.get('filterphone:stats:images_blocked') || 0);
    const passed = parseInt(await r.get('filterphone:stats:images_passed') || 0);
    const cache_hits = parseInt(await r.get('filterphone:stats:cache_hits') || 0);

    res.json({
      images_scanned: scanned,
      images_blocked: blocked,
      images_passed: passed,
      cache_hits,
      block_rate: scanned > 0 ? ((blocked / scanned) * 100).toFixed(1) : '0',
    });
  } catch (e) {
    res.status(500).json({ error: `שגיאה בקריאת סטטיסטיקות: ${e.message}` });
  }
});

// ===== GET /api/image-filter/status =====
// סטטוס שירותים: mitmproxy, Redis
router.get('/status', authenticateToken, requireAdmin, async (req, res) => {
  const status = {
    mitm: { running: false, pid: null },
    redis: { running: false },
  };

  // בדיקת mitmproxy
  try {
    const out = execSync(`systemctl is-active ${MITM_SERVICE} 2>/dev/null || echo inactive`).toString().trim();
    status.mitm.running = out === 'active';
    if (status.mitm.running) {
      const pid = execSync(`systemctl show -p MainPID ${MITM_SERVICE} 2>/dev/null | cut -d= -f2`).toString().trim();
      status.mitm.pid = parseInt(pid) || null;
    }
  } catch { /* שירות לא קיים */ }

  // בדיקת Redis
  try {
    const r = redisClient.getClient();
    if (r) {
      await r.ping();
      status.redis.running = true;
    }
  } catch { /* Redis לא זמין */ }

  res.json(status);
});

// ===== GET /api/image-filter/ca-cert =====
// הורדת CA certificate של mitmproxy
router.get('/ca-cert', (req, res) => {
  const certPath = path.resolve('/root/.mitmproxy/mitmproxy-ca-cert.pem');
  try {
    // ננסה להעתיק את התעודה אם לא נגישה
    const publicCert = path.join(__dirname, '../../public/ca-cert.pem');
    if (!fs.existsSync(publicCert)) {
      execSync(`sudo cp ${certPath} ${publicCert} 2>/dev/null && sudo chmod 644 ${publicCert}`, { timeout: 5000 });
    }
    if (fs.existsSync(publicCert)) {
      res.setHeader('Content-Type', 'application/x-pem-file');
      res.setHeader('Content-Disposition', 'attachment; filename="filterphone-ca.pem"');
      return res.sendFile(publicCert);
    }
    res.status(404).json({ error: 'תעודת CA לא נמצאה. הפעל את mitmproxy פעם אחת כדי ליצור אותה.' });
  } catch (e) {
    res.status(500).json({ error: `שגיאה בקריאת תעודה: ${e.message}` });
  }
});

// ===== GET /api/image-filter/domains =====
// רשימת דומיינים מסוננים
router.get('/domains', authenticateToken, requireAdmin, (req, res) => {
  try {
    if (!fs.existsSync(DOMAINS_PATH)) {
      return res.json({ domains: [] });
    }
    const content = fs.readFileSync(DOMAINS_PATH, 'utf-8');
    const domains = content.split('\n')
      .map(l => l.trim())
      .filter(l => l && !l.startsWith('#'));

    res.json({ domains, total: domains.length });
  } catch (e) {
    res.status(500).json({ error: `שגיאה בקריאת דומיינים: ${e.message}` });
  }
});

// ===== POST /api/image-filter/domains =====
// הוספת דומיין
router.post('/domains', authenticateToken, requireAdmin, (req, res) => {
  const { domain } = req.body;
  if (!domain || typeof domain !== 'string') {
    return res.status(400).json({ error: 'נדרש שדה domain' });
  }

  const clean = domain.trim().toLowerCase();
  if (!/^[\*\.a-z0-9\-]+\.[a-z]{2,}$/.test(clean)) {
    return res.status(400).json({ error: 'פורמט דומיין לא תקין. דוגמה: *.example.com' });
  }

  try {
    const content = fs.existsSync(DOMAINS_PATH)
      ? fs.readFileSync(DOMAINS_PATH, 'utf-8')
      : '';

    const existing = content.split('\n').map(l => l.trim());
    if (existing.includes(clean)) {
      return res.status(409).json({ error: 'הדומיין כבר קיים ברשימה' });
    }

    fs.appendFileSync(DOMAINS_PATH, `\n${clean}\n`, 'utf-8');
    res.json({ message: `הדומיין ${clean} נוסף`, domain: clean });
  } catch (e) {
    res.status(500).json({ error: `שגיאה בהוספת דומיין: ${e.message}` });
  }
});

// ===== DELETE /api/image-filter/domains/:domain =====
// הסרת דומיין
router.delete('/domains/:domain', authenticateToken, requireAdmin, (req, res) => {
  const domain = decodeURIComponent(req.params.domain).trim().toLowerCase();

  try {
    if (!fs.existsSync(DOMAINS_PATH)) {
      return res.status(404).json({ error: 'קובץ דומיינים לא נמצא' });
    }

    const content = fs.readFileSync(DOMAINS_PATH, 'utf-8');
    const lines = content.split('\n');
    const filtered = lines.filter(l => l.trim() !== domain);

    if (filtered.length === lines.length) {
      return res.status(404).json({ error: 'הדומיין לא נמצא ברשימה' });
    }

    fs.writeFileSync(DOMAINS_PATH, filtered.join('\n'), 'utf-8');
    res.json({ message: `הדומיין ${domain} הוסר` });
  } catch (e) {
    res.status(500).json({ error: `שגיאה בהסרת דומיין: ${e.message}` });
  }
});

// ===== POST /api/image-filter/restart =====
// הפעלה מחדש של mitmproxy
router.post('/restart', authenticateToken, requireAdmin, (req, res) => {
  exec(`sudo systemctl restart ${MITM_SERVICE}`, (err, stdout, stderr) => {
    if (err) {
      return res.status(500).json({
        error: `שגיאה בהפעלה מחדש: ${stderr || err.message}`,
      });
    }
    res.json({ message: 'שרת סינון התמונות הופעל מחדש בהצלחה' });
  });
});

module.exports = router;
