// ===================================
// נתיבי אימות - התחברות ורישום
// ===================================
const express = require('express');
const bcrypt = require('bcrypt');
const router = express.Router();

const { getDb } = require('../database');
const { generateToken, authenticateToken } = require('../middleware/auth');

// POST /api/auth/login - התחברות
router.post('/login', async (req, res) => {
  const { email, password } = req.body;

  if (!email || !password) {
    return res.status(400).json({ error: 'נדרשים אימייל וסיסמה' });
  }

  const db = getDb();
  const user = db.prepare('SELECT * FROM users WHERE email = ?').get(email.toLowerCase().trim());

  if (!user) {
    return res.status(401).json({ error: 'אימייל או סיסמה שגויים' });
  }

  if (user.status === 'suspended') {
    return res.status(403).json({ error: 'החשבון שלך הושעה. פנה למנהל המערכת' });
  }

  const validPassword = await bcrypt.compare(password, user.password_hash);
  if (!validPassword) {
    return res.status(401).json({ error: 'אימייל או סיסמה שגויים' });
  }

  // רישום לוג כניסה
  db.prepare('INSERT INTO logs (user_id, action, detail, level) VALUES (?, ?, ?, ?)').run(
    user.id, 'login', `כניסה מוצלחת מ-${req.ip}`, 'info'
  );

  const token = generateToken(user);

  // החזרת פרטי משתמש ללא סיסמה
  const { password_hash, vpn_private_key, ...userWithoutSensitive } = user;

  res.json({
    message: 'התחברת בהצלחה',
    token,
    user: userWithoutSensitive
  });
});

// POST /api/auth/register - רישום לקוח חדש (דרך האפליקציה בעתיד)
router.post('/register', async (req, res) => {
  const { name, phone, email, password, device, platform } = req.body;

  if (!name || !email || !password) {
    return res.status(400).json({ error: 'נדרשים שם, אימייל וסיסמה' });
  }

  if (password.length < 6) {
    return res.status(400).json({ error: 'הסיסמה חייבת להכיל לפחות 6 תווים' });
  }

  const db = getDb();
  const existing = db.prepare('SELECT id FROM users WHERE email = ?').get(email.toLowerCase().trim());

  if (existing) {
    return res.status(409).json({ error: 'כתובת אימייל זו כבר רשומה במערכת' });
  }

  const password_hash = await bcrypt.hash(password, 10);

  const result = db.prepare(`
    INSERT INTO users (name, phone, email, password_hash, role, device, platform, filter_level, status)
    VALUES (?, ?, ?, ?, 'client', ?, ?, 1, 'active')
  `).run(name, phone || null, email.toLowerCase().trim(), password_hash, device || null, platform || null);

  db.prepare('INSERT INTO logs (user_id, action, detail, level) VALUES (?, ?, ?, ?)').run(
    result.lastInsertRowid, 'register', 'משתמש חדש נרשם', 'info'
  );

  const newUser = db.prepare('SELECT * FROM users WHERE id = ?').get(result.lastInsertRowid);
  const token = generateToken(newUser);
  const { password_hash: ph, vpn_private_key, ...userWithoutSensitive } = newUser;

  res.status(201).json({
    message: 'נרשמת בהצלחה',
    token,
    user: userWithoutSensitive
  });
});

// GET /api/auth/me - פרטי המשתמש המחובר
router.get('/me', authenticateToken, (req, res) => {
  const db = getDb();
  const user = db.prepare('SELECT * FROM users WHERE id = ?').get(req.user.id);

  if (!user) {
    return res.status(404).json({ error: 'משתמש לא נמצא' });
  }

  const { password_hash, vpn_private_key, ...userWithoutSensitive } = user;
  res.json(userWithoutSensitive);
});

module.exports = router;
