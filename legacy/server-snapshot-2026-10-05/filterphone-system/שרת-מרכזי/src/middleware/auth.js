// ===================================
// Middleware לאימות JWT ובדיקת הרשאות
// ===================================
const jwt = require('jsonwebtoken');

const JWT_SECRET = process.env.JWT_SECRET || 'REDACTED';

// בדיקת טוקן JWT תקין
function authenticateToken(req, res, next) {
  const authHeader = req.headers['authorization'];
  const token = authHeader && authHeader.split(' ')[1]; // Bearer TOKEN

  if (!token) {
    return res.status(401).json({ error: 'נדרשת התחברות' });
  }

  jwt.verify(token, JWT_SECRET, (err, user) => {
    if (err) {
      return res.status(403).json({ error: 'טוקן לא תקין או פג תוקף' });
    }
    req.user = user;
    next();
  });
}

// בדיקה שהמשתמש הוא מנהל
function requireAdmin(req, res, next) {
  if (!req.user || req.user.role !== 'admin') {
    return res.status(403).json({ error: 'פעולה זו מותרת למנהלים בלבד' });
  }
  next();
}

// יצירת טוקן חדש
function generateToken(user) {
  return jwt.sign(
    { id: user.id, email: user.email, role: user.role },
    JWT_SECRET,
    { expiresIn: '7d' }
  );
}

module.exports = { authenticateToken, requireAdmin, generateToken };
