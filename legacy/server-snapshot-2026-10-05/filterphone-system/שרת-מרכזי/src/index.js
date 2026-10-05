// ===================================
// שרת מרכזי - FilterPhone
// פורט: 3001
// ===================================
const express = require('express');
const cors = require('cors');
const { initDatabase } = require('./database');

const authRoutes = require('./routes/auth');
const usersRoutes = require('./routes/users');
const requestsRoutes = require('./routes/requests');
const logsRoutes = require('./routes/logs');
const vpnRoutes = require('./routes/vpn');
const adguardRoutes = require('./routes/adguard');
const imageFilterRoutes = require('./routes/image-filter');

const app = express();
const PORT = process.env.PORT || 3001;

// Middleware בסיסי
app.use(cors({
  origin: '*',
  methods: ['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS'],
  allowedHeaders: ['Content-Type', 'Authorization']
}));
app.use(express.json());
app.use(express.urlencoded({ extended: true }));

// לוגים בסיסיים לכל בקשה
app.use((req, res, next) => {
  console.log(`[${new Date().toLocaleString('he-IL')}] ${req.method} ${req.path}`);
  next();
});

// נתיבי API
app.use('/api/auth', authRoutes);
app.use('/api/users', usersRoutes);
app.use('/api/requests', requestsRoutes);
app.use('/api/logs', logsRoutes);
app.use('/api/vpn', vpnRoutes);
app.use('/api/adguard', adguardRoutes);
app.use('/api/image-filter', imageFilterRoutes);
app.use('/api/download', imageFilterRoutes);

// הגשת קבצים סטטיים (CA cert)
app.use('/public', require('express').static(require('path').join(__dirname, '../public')));

// נתיב סטטיסטיקות - מיפוי מיוחד
app.use('/api/stats', (req, res, next) => {
  req.url = '/stats';
  logsRoutes(req, res, next);
});

// בדיקת חיות שרת
app.get('/health', (req, res) => {
  res.json({
    status: 'ok',
    message: 'שרת FilterPhone פועל',
    timestamp: new Date().toISOString(),
    version: '1.0.0'
  });
});

// טיפול בשגיאות כלליות
app.use((err, req, res, next) => {
  console.error('שגיאת שרת:', err);
  res.status(500).json({ error: 'שגיאת שרת פנימית' });
});

// נתיב לא נמצא
app.use((req, res) => {
  res.status(404).json({ error: `הנתיב ${req.path} לא נמצא` });
});

// אתחול מסד הנתונים והפעלת השרת
async function startServer() {
  try {
    await initDatabase();
    app.listen(PORT, '0.0.0.0', () => {
      console.log('');
      console.log('🚀 ========================================');
      console.log(`🚀  שרת FilterPhone פועל על פורט ${PORT}`);
      console.log('🚀 ========================================');
      console.log('');
      console.log('📋 נתיבים זמינים:');
      console.log(`   POST http://localhost:${PORT}/api/auth/login`);
      console.log(`   GET  http://localhost:${PORT}/api/users`);
      console.log(`   GET  http://localhost:${PORT}/api/stats`);
      console.log(`   GET  http://localhost:${PORT}/health`);
      console.log('');
      console.log('👤 פרטי כניסה ראשוניים:');
      console.log('   מנהל: admin@filterphone.com / REDACTED');
      console.log('   לקוח: israel@example.com / REDACTED');
      console.log('');
    });
  } catch (error) {
    console.error('❌ שגיאה באתחול השרת:', error);
    process.exit(1);
  }
}

startServer();
