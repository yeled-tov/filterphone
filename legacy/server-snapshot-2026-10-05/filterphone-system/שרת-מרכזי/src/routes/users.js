// ===================================
// נתיבי ניהול משתמשים
// ===================================
const express = require('express');
const bcrypt = require('bcrypt');
const router = express.Router();

const { getDb } = require('../database');
const { authenticateToken, requireAdmin } = require('../middleware/auth');
const redisClient = require('../redis-client');

// פונקציה עזר - הסרת שדות רגישים
function sanitizeUser(user) {
  const { password_hash, vpn_private_key, ...safe } = user;
  return safe;
}

// GET /api/users/me - פרטי המשתמש המחובר
router.get('/me', authenticateToken, (req, res) => {
  const db = getDb();
  const user = db.prepare('SELECT * FROM users WHERE id = ?').get(req.user.id);
  if (!user) return res.status(404).json({ error: 'משתמש לא נמצא' });
  res.json(sanitizeUser(user));
});

// GET /api/users - כל המשתמשים (מנהל בלבד)
router.get('/', authenticateToken, requireAdmin, (req, res) => {
  const db = getDb();
  const users = db.prepare('SELECT * FROM users ORDER BY created_at DESC').all();
  res.json(users.map(sanitizeUser));
});

// POST /api/users - יצירת משתמש חדש (מנהל בלבד)
router.post('/', authenticateToken, requireAdmin, async (req, res) => {
  const { name, phone, email, password, role, device, platform, filter_level, status, subscription_expires_at } = req.body;

  if (!name || !email || !password) {
    return res.status(400).json({ error: 'נדרשים שם, אימייל וסיסמה' });
  }

  const db = getDb();
  const existing = db.prepare('SELECT id FROM users WHERE email = ?').get(email.toLowerCase().trim());
  if (existing) {
    return res.status(409).json({ error: 'כתובת אימייל זו כבר קיימת במערכת' });
  }

  const password_hash = await bcrypt.hash(password, 10);
  const newFilterLevel = filter_level || 1;

  const result = db.prepare(`
    INSERT INTO users (name, phone, email, password_hash, role, device, platform, filter_level, status, subscription_expires_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `).run(
    name,
    phone || null,
    email.toLowerCase().trim(),
    password_hash,
    role || 'client',
    device || null,
    platform || null,
    newFilterLevel,
    status || 'active',
    subscription_expires_at || null
  );

  db.prepare('INSERT INTO logs (user_id, action, detail, level) VALUES (?, ?, ?, ?)').run(
    req.user.id, 'create_user', `נוצר משתמש חדש: ${email}`, 'info'
  );

  const newUser = db.prepare('SELECT * FROM users WHERE id = ?').get(result.lastInsertRowid);

  // סנכרון רמת סינון ל-Redis (אם יש vpn_ip)
  if (newUser.vpn_ip) {
    await redisClient.syncFilterLevel(newUser.vpn_ip, newFilterLevel);
  }

  res.status(201).json(sanitizeUser(newUser));
});

// PUT /api/users/:id - עדכון משתמש (מנהל בלבד)
router.put('/:id', authenticateToken, requireAdmin, async (req, res) => {
  const { id } = req.params;
  const { name, phone, email, password, device, platform, filter_level, status, subscription_expires_at } = req.body;

  const db = getDb();
  const user = db.prepare('SELECT * FROM users WHERE id = ?').get(id);
  if (!user) return res.status(404).json({ error: 'משתמש לא נמצא' });

  // בדיקת כפילות אימייל
  if (email && email !== user.email) {
    const existing = db.prepare('SELECT id FROM users WHERE email = ? AND id != ?').get(email.toLowerCase().trim(), id);
    if (existing) return res.status(409).json({ error: 'כתובת אימייל זו כבר קיימת' });
  }

  let password_hash = user.password_hash;
  if (password) {
    password_hash = await bcrypt.hash(password, 10);
  }

  const newFilterLevel = filter_level || user.filter_level;

  db.prepare(`
    UPDATE users SET
      name = ?,
      phone = ?,
      email = ?,
      password_hash = ?,
      device = ?,
      platform = ?,
      filter_level = ?,
      status = ?,
      subscription_expires_at = ?
    WHERE id = ?
  `).run(
    name || user.name,
    phone !== undefined ? phone : user.phone,
    email ? email.toLowerCase().trim() : user.email,
    password_hash,
    device !== undefined ? device : user.device,
    platform !== undefined ? platform : user.platform,
    newFilterLevel,
    status || user.status,
    subscription_expires_at !== undefined ? subscription_expires_at : user.subscription_expires_at,
    id
  );

  db.prepare('INSERT INTO logs (user_id, action, detail, level) VALUES (?, ?, ?, ?)').run(
    req.user.id, 'update_user', `עודכן משתמש ID: ${id}`, 'info'
  );

  // סנכרון רמת סינון ל-Redis אם השתנתה
  if (filter_level && filter_level !== user.filter_level) {
    const updated = db.prepare('SELECT vpn_ip FROM users WHERE id = ?').get(id);
    if (updated && updated.vpn_ip) {
      await redisClient.syncFilterLevel(updated.vpn_ip, newFilterLevel);
    }
  }

  const updated = db.prepare('SELECT * FROM users WHERE id = ?').get(id);
  res.json(sanitizeUser(updated));
});

// DELETE /api/users/:id - מחיקת משתמש (מנהל בלבד)
router.delete('/:id', authenticateToken, requireAdmin, (req, res) => {
  const { id } = req.params;
  const db = getDb();

  // אי אפשר למחוק את עצמך
  if (parseInt(id) === req.user.id) {
    return res.status(400).json({ error: 'לא ניתן למחוק את המשתמש המחובר' });
  }

  const user = db.prepare('SELECT * FROM users WHERE id = ?').get(id);
  if (!user) return res.status(404).json({ error: 'משתמש לא נמצא' });

  db.prepare('DELETE FROM users WHERE id = ?').run(id);

  db.prepare('INSERT INTO logs (user_id, action, detail, level) VALUES (?, ?, ?, ?)').run(
    req.user.id, 'delete_user', `נמחק משתמש: ${user.email}`, 'warning'
  );

  res.json({ message: 'המשתמש נמחק בהצלחה' });
});

// PUT /api/users/:id/filter-level - שינוי רמת סינון (מנהל בלבד)
router.put('/:id/filter-level', authenticateToken, requireAdmin, async (req, res) => {
  const { id } = req.params;
  const { filter_level } = req.body;

  if (![1, 2, 3].includes(filter_level)) {
    return res.status(400).json({ error: 'רמת סינון חייבת להיות 1, 2 או 3' });
  }

  const db = getDb();
  const user = db.prepare('SELECT * FROM users WHERE id = ?').get(id);
  if (!user) return res.status(404).json({ error: 'משתמש לא נמצא' });

  db.prepare('UPDATE users SET filter_level = ? WHERE id = ?').run(filter_level, id);

  // סנכרון ל-Redis
  if (user.vpn_ip) {
    await redisClient.syncFilterLevel(user.vpn_ip, filter_level);
  }

  const levelNames = { 1: 'חסימה מלאה', 2: 'טשטוש חלקי', 3: 'צניעות בלבד' };
  db.prepare('INSERT INTO logs (user_id, action, detail, level) VALUES (?, ?, ?, ?)').run(
    req.user.id, 'filter_level_change',
    `רמת סינון של ${user.name} שונתה ל-${levelNames[filter_level]}`,
    'info'
  );

  res.json({ message: `רמת הסינון עודכנה ל-${levelNames[filter_level]}`, filter_level });
});

// GET /api/users/:id/effective-level - הרמה האמיתית שmitmproxy רואה (מ-Redis)
router.get('/:id/effective-level', authenticateToken, requireAdmin, async (req, res) => {
  const { id } = req.params;
  const db = getDb();
  const user = db.prepare('SELECT vpn_ip, filter_level FROM users WHERE id = ?').get(id);
  if (!user) return res.status(404).json({ error: 'משתמש לא נמצא' });

  let redisLevel = null;
  try {
    const r = redisClient.getClient();
    if (r && user.vpn_ip) {
      const val = await r.get(`filterphone:vpn_ip:${user.vpn_ip}:filter_level`);
      redisLevel = val ? parseInt(val) : null;
    }
  } catch (e) {
    // Redis לא זמין
  }

  res.json({
    db_level: user.filter_level,
    redis_level: redisLevel,
    vpn_ip: user.vpn_ip,
    synced: redisLevel === user.filter_level,
  });
});

// PUT /api/users/:id/status - שינוי סטטוס (מנהל בלבד)
router.put('/:id/status', authenticateToken, requireAdmin, (req, res) => {
  const { id } = req.params;
  const { status } = req.body;

  if (!['active', 'inactive', 'suspended'].includes(status)) {
    return res.status(400).json({ error: 'סטטוס לא תקין. האפשרויות: active, inactive, suspended' });
  }

  const db = getDb();
  const user = db.prepare('SELECT * FROM users WHERE id = ?').get(id);
  if (!user) return res.status(404).json({ error: 'משתמש לא נמצא' });

  db.prepare('UPDATE users SET status = ? WHERE id = ?').run(status, id);

  const statusNames = { active: 'פעיל', inactive: 'לא פעיל', suspended: 'מושעה' };
  db.prepare('INSERT INTO logs (user_id, action, detail, level) VALUES (?, ?, ?, ?)').run(
    req.user.id, 'status_change',
    `סטטוס של ${user.name} שונה ל-${statusNames[status]}`,
    status === 'suspended' ? 'warning' : 'info'
  );

  res.json({ message: `הסטטוס עודכן ל-${statusNames[status]}`, status });
});

module.exports = router;
