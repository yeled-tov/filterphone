// ===================================
// נתיבי בקשות לקוחות
// ===================================
const express = require('express');
const router = express.Router();

const { getDb } = require('../database');
const { authenticateToken, requireAdmin } = require('../middleware/auth');
const redisClient = require('../redis-client');

// POST /api/requests - שליחת בקשה חדשה (לקוח)
router.post('/', authenticateToken, (req, res) => {
  const { type, requested_level, reason } = req.body;

  if (!type || !['level_change', 'pause', 'resume'].includes(type)) {
    return res.status(400).json({ error: 'סוג בקשה לא תקין' });
  }

  if (type === 'level_change' && ![1, 2, 3].includes(requested_level)) {
    return res.status(400).json({ error: 'בקשת שינוי רמה חייבת לכלול רמה תקינה (1-3)' });
  }

  const db = getDb();

  // בדיקה שאין בקשה פתוחה מאותו סוג
  const existing = db.prepare(
    "SELECT id FROM requests WHERE user_id = ? AND type = ? AND status = 'pending'"
  ).get(req.user.id, type);

  if (existing) {
    return res.status(409).json({ error: 'כבר קיימת בקשה פתוחה מסוג זה. המתן לטיפול' });
  }

  const result = db.prepare(`
    INSERT INTO requests (user_id, type, requested_level, reason, status)
    VALUES (?, ?, ?, ?, 'pending')
  `).run(req.user.id, type, requested_level || null, reason || null);

  const typeNames = { level_change: 'שינוי רמת סינון', pause: 'השהיית סינון', resume: 'חידוש סינון' };
  db.prepare('INSERT INTO logs (user_id, action, detail, level) VALUES (?, ?, ?, ?)').run(
    req.user.id, 'request_created', `בקשה חדשה: ${typeNames[type]}`, 'info'
  );

  const newRequest = db.prepare('SELECT * FROM requests WHERE id = ?').get(result.lastInsertRowid);
  res.status(201).json(newRequest);
});

// GET /api/requests - כל הבקשות (מנהל) או הבקשות של הלקוח
router.get('/', authenticateToken, (req, res) => {
  const db = getDb();

  if (req.user.role === 'admin') {
    // מנהל רואה הכל עם פרטי המשתמש
    const requests = db.prepare(`
      SELECT r.*, u.name as user_name, u.email as user_email, u.phone as user_phone
      FROM requests r
      JOIN users u ON r.user_id = u.id
      ORDER BY r.created_at DESC
    `).all();
    return res.json(requests);
  }

  // לקוח רואה רק את הבקשות שלו
  const requests = db.prepare(
    'SELECT * FROM requests WHERE user_id = ? ORDER BY created_at DESC'
  ).all(req.user.id);
  res.json(requests);
});

// PUT /api/requests/:id/approve - אישור בקשה (מנהל בלבד)
router.put('/:id/approve', authenticateToken, requireAdmin, async (req, res) => {
  const { id } = req.params;
  const db = getDb();

  const request = db.prepare('SELECT * FROM requests WHERE id = ?').get(id);
  if (!request) return res.status(404).json({ error: 'בקשה לא נמצאה' });

  if (request.status !== 'pending') {
    return res.status(400).json({ error: 'ניתן לאשר רק בקשות בסטטוס "ממתין"' });
  }

  // עדכון הבקשה
  db.prepare(`
    UPDATE requests SET status = 'approved', resolved_at = datetime('now') WHERE id = ?
  `).run(id);

  // ביצוע הפעולה בפועל
  if (request.type === 'level_change' && request.requested_level) {
    db.prepare('UPDATE users SET filter_level = ? WHERE id = ?').run(request.requested_level, request.user_id);

    // סנכרון ל-Redis
    const user = db.prepare('SELECT vpn_ip FROM users WHERE id = ?').get(request.user_id);
    if (user && user.vpn_ip) {
      await redisClient.syncFilterLevel(user.vpn_ip, request.requested_level);
    }
  } else if (request.type === 'pause') {
    db.prepare("UPDATE users SET status = 'inactive' WHERE id = ?").run(request.user_id);
  } else if (request.type === 'resume') {
    db.prepare("UPDATE users SET status = 'active' WHERE id = ?").run(request.user_id);
  }

  db.prepare('INSERT INTO logs (user_id, action, detail, level) VALUES (?, ?, ?, ?)').run(
    req.user.id, 'request_approved', `בקשה #${id} אושרה`, 'info'
  );

  res.json({ message: 'הבקשה אושרה בהצלחה' });
});

// PUT /api/requests/:id/reject - דחיית בקשה (מנהל בלבד)
router.put('/:id/reject', authenticateToken, requireAdmin, (req, res) => {
  const { id } = req.params;
  const { reason } = req.body;
  const db = getDb();

  const request = db.prepare('SELECT * FROM requests WHERE id = ?').get(id);
  if (!request) return res.status(404).json({ error: 'בקשה לא נמצאה' });

  if (request.status !== 'pending') {
    return res.status(400).json({ error: 'ניתן לדחות רק בקשות בסטטוס "ממתין"' });
  }

  db.prepare(`
    UPDATE requests SET status = 'rejected', resolved_at = datetime('now') WHERE id = ?
  `).run(id);

  db.prepare('INSERT INTO logs (user_id, action, detail, level) VALUES (?, ?, ?, ?)').run(
    req.user.id, 'request_rejected', `בקשה #${id} נדחתה${reason ? `: ${reason}` : ''}`, 'warning'
  );

  res.json({ message: 'הבקשה נדחתה' });
});

module.exports = router;
