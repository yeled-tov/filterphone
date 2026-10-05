// ===================================
// נתיבי VPN - WireGuard
// POST /api/vpn/generate/:userId   - יצירת קונפיג (מנהל)
// GET  /api/vpn/my-config          - קובץ הקונפיג שלי (לקוח)
// GET  /api/vpn/my-qr              - QR Code שלי (לקוח)
// DELETE /api/vpn/:userId          - מחיקת VPN של לקוח (מנהל)
// ===================================
const express = require('express');
const router = express.Router();
const { execSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const { getDb } = require('../database');
const { authenticateToken, requireAdmin } = require('../middleware/auth');
const redisClient = require('../redis-client');

// תיקיית קבצי ה-VPN
const VPN_DIR = '/home/ubuntu/vpn-clients';
// נתיב לסקריפטים
const SCRIPTS_DIR = path.join(__dirname, '../../../שרת-VPN-ו-DNS');

// פונקציה עזר - הרצת סקריפט בבטחה
function runScript(scriptName, args = []) {
  const scriptPath = path.join(SCRIPTS_DIR, scriptName);

  if (!fs.existsSync(scriptPath)) {
    throw new Error(`סקריפט לא נמצא: ${scriptName}. האם install-wireguard.sh רץ?`);
  }

  const cmd = `sudo bash "${scriptPath}" ${args.map(a => `"${a}"`).join(' ')}`;
  try {
    const output = execSync(cmd, {
      timeout: 60000,
      encoding: 'utf8',
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    return output;
  } catch (err) {
    const msg = err.stderr || err.stdout || err.message;
    throw new Error(`שגיאה בהרצת ${scriptName}: ${msg}`);
  }
}

// ----- POST /api/vpn/generate/:userId -----
// יצירת קובץ VPN ללקוח (מנהל בלבד)
router.post('/generate/:userId', authenticateToken, requireAdmin, async (req, res) => {
  const { userId } = req.params;
  const db = getDb();

  const user = db.prepare('SELECT * FROM users WHERE id = ?').get(userId);
  if (!user) {
    return res.status(404).json({ error: 'משתמש לא נמצא' });
  }

  if (user.role === 'admin') {
    return res.status(400).json({ error: 'לא ניתן לייצר VPN לחשבון מנהל' });
  }

  try {
    const output = runScript('create-client.sh', [userId, user.name]);
    console.log(`[VPN] נוצר קונפיג ל-${user.name} (ID: ${userId}):`, output.slice(-200));

    const confPath = path.join(VPN_DIR, `${userId}.conf`);
    if (!fs.existsSync(confPath)) {
      return res.status(500).json({ error: 'הסקריפט רץ אך קובץ הקונפיג לא נמצא' });
    }

    const vpnConfig = fs.readFileSync(confPath, 'utf8');
    const vpnIp = fs.existsSync(path.join(VPN_DIR, `${userId}.ip`))
      ? fs.readFileSync(path.join(VPN_DIR, `${userId}.ip`), 'utf8').trim()
      : null;

    // שמירה ב-DB
    db.prepare('UPDATE users SET vpn_config = ?, vpn_ip = ? WHERE id = ?')
      .run(vpnConfig, vpnIp, userId);

    // סנכרון רמת סינון ל-Redis עם ה-VPN IP החדש
    if (vpnIp) {
      await redisClient.syncFilterLevel(vpnIp, user.filter_level || 1);
    }

    db.prepare('INSERT INTO logs (user_id, action, detail, level) VALUES (?, ?, ?, ?)')
      .run(req.user.id, 'vpn_created', `VPN נוצר עבור ${user.name} (IP: ${vpnIp})`, 'info');

    res.json({
      message: 'קובץ VPN נוצר בהצלחה',
      userId,
      vpnIp,
      config: vpnConfig,
    });

  } catch (err) {
    console.error('[VPN] שגיאה ביצירת קונפיג:', err.message);
    res.status(500).json({ error: err.message });
  }
});

// ----- GET /api/vpn/my-config -----
router.get('/my-config', authenticateToken, (req, res) => {
  const db = getDb();
  const user = db.prepare('SELECT vpn_config, vpn_ip FROM users WHERE id = ?').get(req.user.id);

  if (!user || !user.vpn_config) {
    return res.status(404).json({ error: 'לא נוצר קובץ VPN עבורך עדיין. פנה למנהל.' });
  }

  res.setHeader('Content-Type', 'text/plain; charset=utf-8');
  res.setHeader('Content-Disposition', `attachment; filename="filterphone-vpn.conf"`);
  res.send(user.vpn_config);
});

// ----- GET /api/vpn/my-qr -----
router.get('/my-qr', authenticateToken, (req, res) => {
  const db = getDb();
  const user = db.prepare('SELECT vpn_config FROM users WHERE id = ?').get(req.user.id);

  if (!user || !user.vpn_config) {
    return res.status(404).json({ error: 'לא נוצר קובץ VPN עבורך עדיין. פנה למנהל.' });
  }

  const qrPath = path.join(VPN_DIR, `${req.user.id}.png`);
  if (fs.existsSync(qrPath)) {
    const qrData = fs.readFileSync(qrPath);
    const base64 = qrData.toString('base64');
    return res.json({ qr: `data:image/png;base64,${base64}` });
  }

  try {
    const tmpConf = `/tmp/vpn_${req.user.id}_${Date.now()}.conf`;
    fs.writeFileSync(tmpConf, user.vpn_config, { mode: 0o600 });

    const qrBase64 = execSync(`qrencode -t PNG -o - < "${tmpConf}" | base64`, {
      timeout: 10000,
      encoding: 'utf8',
    }).trim();

    fs.unlinkSync(tmpConf);
    res.json({ qr: `data:image/png;base64,${qrBase64}` });

  } catch (err) {
    console.error('[VPN] שגיאה ביצירת QR:', err.message);
    res.status(500).json({ error: 'לא ניתן לייצר QR code. ודא ש-qrencode מותקן.' });
  }
});

// ----- GET /api/vpn/:userId/config -----
router.get('/:userId/config', authenticateToken, requireAdmin, (req, res) => {
  const { userId } = req.params;
  const db = getDb();

  const user = db.prepare('SELECT vpn_config, name FROM users WHERE id = ?').get(userId);
  if (!user || !user.vpn_config) {
    return res.status(404).json({ error: 'לא נמצא קונפיג VPN עבור משתמש זה' });
  }

  res.setHeader('Content-Type', 'text/plain; charset=utf-8');
  res.setHeader('Content-Disposition', `attachment; filename="filterphone-${userId}.conf"`);
  res.send(user.vpn_config);
});

// ----- GET /api/vpn/:userId/qr -----
router.get('/:userId/qr', authenticateToken, requireAdmin, (req, res) => {
  const { userId } = req.params;
  const db = getDb();

  const user = db.prepare('SELECT vpn_config FROM users WHERE id = ?').get(userId);
  if (!user || !user.vpn_config) {
    return res.status(404).json({ error: 'לא נמצא קונפיג VPN עבור משתמש זה' });
  }

  const qrPath = path.join(VPN_DIR, `${userId}.png`);
  if (fs.existsSync(qrPath)) {
    const qrData = fs.readFileSync(qrPath);
    const base64 = qrData.toString('base64');
    return res.json({ qr: `data:image/png;base64,${base64}` });
  }

  try {
    const tmpConf = `/tmp/vpn_admin_${userId}_${Date.now()}.conf`;
    fs.writeFileSync(tmpConf, user.vpn_config, { mode: 0o600 });

    const qrBase64 = execSync(`qrencode -t PNG -o - < "${tmpConf}" | base64`, {
      timeout: 10000,
      encoding: 'utf8',
    }).trim();

    fs.unlinkSync(tmpConf);
    res.json({ qr: `data:image/png;base64,${qrBase64}` });
  } catch (err) {
    console.error('[VPN] שגיאה ביצירת QR:', err.message);
    res.status(500).json({ error: 'לא ניתן לייצר QR code. ודא ש-qrencode מותקן.' });
  }
});

// ----- DELETE /api/vpn/:userId -----
router.delete('/:userId', authenticateToken, requireAdmin, (req, res) => {
  const { userId } = req.params;
  const db = getDb();

  const user = db.prepare('SELECT * FROM users WHERE id = ?').get(userId);
  if (!user) {
    return res.status(404).json({ error: 'משתמש לא נמצא' });
  }

  if (!user.vpn_config && !fs.existsSync(path.join(VPN_DIR, `${userId}.conf`))) {
    return res.status(404).json({ error: 'לא נמצא VPN עבור משתמש זה' });
  }

  try {
    runScript('delete-client.sh', [userId]);

    db.prepare('UPDATE users SET vpn_config = NULL, vpn_ip = NULL, vpn_public_key = NULL, vpn_private_key = NULL WHERE id = ?')
      .run(userId);

    db.prepare('INSERT INTO logs (user_id, action, detail, level) VALUES (?, ?, ?, ?)')
      .run(req.user.id, 'vpn_deleted', `VPN נמחק עבור ${user.name}`, 'warning');

    res.json({ message: 'VPN נמחק בהצלחה' });

  } catch (err) {
    console.error('[VPN] שגיאה במחיקה:', err.message);
    res.status(500).json({ error: err.message });
  }
});

module.exports = router;
