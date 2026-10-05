// ===================================
// נתיבי לוגים וסטטיסטיקות
// ===================================
const express = require('express');
const router = express.Router();

const { getDb } = require('../database');
const { authenticateToken, requireAdmin } = require('../middleware/auth');

// GET /api/logs - לוגים (מנהל בלבד)
router.get('/', authenticateToken, requireAdmin, (req, res) => {
  const db = getDb();
  const { limit = 100, user_id, level } = req.query;

  let query = `
    SELECT l.*, u.name as user_name, u.email as user_email
    FROM logs l
    LEFT JOIN users u ON l.user_id = u.id
    WHERE 1=1
  `;
  const params = [];

  if (user_id) {
    query += ' AND l.user_id = ?';
    params.push(user_id);
  }

  if (level) {
    query += ' AND l.level = ?';
    params.push(level);
  }

  query += ' ORDER BY l.created_at DESC LIMIT ?';
  params.push(Math.min(parseInt(limit), 500));

  const logs = db.prepare(query).all(...params);
  res.json(logs);
});

// GET /api/stats - סטטיסטיקות כלליות (מנהל בלבד)
router.get('/stats', authenticateToken, requireAdmin, (req, res) => {
  const db = getDb();

  const totalUsers = db.prepare("SELECT COUNT(*) as count FROM users WHERE role = 'client'").get().count;
  const activeUsers = db.prepare("SELECT COUNT(*) as count FROM users WHERE status = 'active' AND role = 'client'").get().count;
  const suspendedUsers = db.prepare("SELECT COUNT(*) as count FROM users WHERE status = 'suspended' AND role = 'client'").get().count;
  const inactiveUsers = db.prepare("SELECT COUNT(*) as count FROM users WHERE status = 'inactive' AND role = 'client'").get().count;

  const pendingRequests = db.prepare("SELECT COUNT(*) as count FROM requests WHERE status = 'pending'").get().count;
  const totalRequests = db.prepare("SELECT COUNT(*) as count FROM requests").get().count;

  // התפלגות רמות סינון
  const filterLevels = db.prepare(`
    SELECT filter_level, COUNT(*) as count
    FROM users WHERE role = 'client'
    GROUP BY filter_level
  `).all();

  // התפלגות פלטפורמות
  const platforms = db.prepare(`
    SELECT platform, COUNT(*) as count
    FROM users WHERE role = 'client' AND platform IS NOT NULL
    GROUP BY platform
  `).all();

  // סה"כ תמונות
  const imageStats = db.prepare(`
    SELECT SUM(images_blocked) as total_blocked, SUM(images_processed) as total_processed
    FROM users WHERE role = 'client'
  `).get();

  // לוגים אחרונים
  const recentActivity = db.prepare(`
    SELECT l.*, u.name as user_name
    FROM logs l
    LEFT JOIN users u ON l.user_id = u.id
    ORDER BY l.created_at DESC
    LIMIT 10
  `).all();

  res.json({
    users: {
      total: totalUsers,
      active: activeUsers,
      inactive: inactiveUsers,
      suspended: suspendedUsers
    },
    requests: {
      total: totalRequests,
      pending: pendingRequests
    },
    filterLevels,
    platforms,
    images: {
      blocked: imageStats.total_blocked || 0,
      processed: imageStats.total_processed || 0
    },
    recentActivity
  });
});

module.exports = router;
