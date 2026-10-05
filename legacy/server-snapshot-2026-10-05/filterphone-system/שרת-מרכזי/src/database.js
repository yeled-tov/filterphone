// ===================================
// אתחול מסד הנתונים SQLite
// ===================================
const Database = require('better-sqlite3');
const bcrypt = require('bcrypt');
const path = require('path');

const DB_PATH = path.join(__dirname, '..', 'filterphone.db');

let db;

function getDb() {
  if (!db) {
    db = new Database(DB_PATH);
    db.pragma('journal_mode = WAL');
    db.pragma('foreign_keys = ON');
  }
  return db;
}

// יצירת כל הטבלאות
function initDatabase() {
  const database = getDb();

  // טבלת משתמשים
  database.exec(`
    CREATE TABLE IF NOT EXISTS users (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL,
      phone TEXT,
      email TEXT UNIQUE NOT NULL,
      password_hash TEXT NOT NULL,
      role TEXT NOT NULL DEFAULT 'client' CHECK(role IN ('admin', 'client')),
      device TEXT,
      platform TEXT CHECK(platform IN ('Android', 'iOS', 'Windows', NULL)),
      filter_level INTEGER NOT NULL DEFAULT 1 CHECK(filter_level IN (1, 2, 3)),
      status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'inactive', 'suspended')),
      created_at TEXT NOT NULL DEFAULT (datetime('now')),
      images_blocked INTEGER NOT NULL DEFAULT 0,
      images_processed INTEGER NOT NULL DEFAULT 0,
      vpn_config TEXT,
      vpn_public_key TEXT,
      vpn_private_key TEXT,
      vpn_ip TEXT,
      subscription_expires_at TEXT
    )
  `);

  // טבלת בקשות
  database.exec(`
    CREATE TABLE IF NOT EXISTS requests (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL,
      type TEXT NOT NULL CHECK(type IN ('level_change', 'pause', 'resume')),
      requested_level INTEGER CHECK(requested_level IN (1, 2, 3)),
      reason TEXT,
      status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'approved', 'rejected')),
      created_at TEXT NOT NULL DEFAULT (datetime('now')),
      resolved_at TEXT,
      FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    )
  `);

  // טבלת לוגים
  database.exec(`
    CREATE TABLE IF NOT EXISTS logs (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER,
      action TEXT NOT NULL,
      detail TEXT,
      level TEXT CHECK(level IN ('info', 'warning', 'error')),
      created_at TEXT NOT NULL DEFAULT (datetime('now')),
      FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
    )
  `);

  // טבלת רשימות חסימה DNS (לעתיד)
  database.exec(`
    CREATE TABLE IF NOT EXISTS dns_blocklists (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL,
      url TEXT NOT NULL,
      enabled INTEGER NOT NULL DEFAULT 1,
      last_updated TEXT
    )
  `);

  // הוספת נתוני דוגמה
  seedDatabase(database);

  console.log('✅ מסד הנתונים אותחל בהצלחה');
  return database;
}

// הוספת נתוני דוגמה ראשוניים
async function seedDatabase(database) {
  const existingAdmin = database.prepare('SELECT id FROM users WHERE email = ?').get('admin@filterphone.com');
  if (existingAdmin) return; // כבר יש נתונים

  console.log('🌱 מוסיף נתוני דוגמה...');

  const adminHash = await bcrypt.hash('REDACTED', 10);
  const clientHash = await bcrypt.hash('REDACTED', 10);

  // הוספת מנהל
  const insertUser = database.prepare(`
    INSERT INTO users (name, phone, email, password_hash, role, device, platform, filter_level, status, subscription_expires_at)
    VALUES (@name, @phone, @email, @password_hash, @role, @device, @platform, @filter_level, @status, @subscription_expires_at)
  `);

  insertUser.run({
    name: 'מנהל ראשי',
    phone: '050-0000000',
    email: 'admin@filterphone.com',
    password_hash: adminHash,
    role: 'admin',
    device: 'Admin PC',
    platform: 'Windows',
    filter_level: 3,
    status: 'active',
    subscription_expires_at: '2027-12-31'
  });

  insertUser.run({
    name: 'ישראל ישראלי',
    phone: '052-1234567',
    email: 'israel@example.com',
    password_hash: clientHash,
    role: 'client',
    device: 'Samsung Galaxy S23',
    platform: 'Android',
    filter_level: 1,
    status: 'active',
    subscription_expires_at: '2026-12-31'
  });

  insertUser.run({
    name: 'שרה כהן',
    phone: '054-7654321',
    email: 'sarah@example.com',
    password_hash: clientHash,
    role: 'client',
    device: 'iPhone 14',
    platform: 'iOS',
    filter_level: 2,
    status: 'active',
    subscription_expires_at: '2026-06-30'
  });

  insertUser.run({
    name: 'דוד לוי',
    phone: '053-9876543',
    email: 'david@example.com',
    password_hash: clientHash,
    role: 'client',
    device: 'Pixel 7',
    platform: 'Android',
    filter_level: 1,
    status: 'inactive',
    subscription_expires_at: '2026-03-01'
  });

  insertUser.run({
    name: 'מרים אברהם',
    phone: '058-1112233',
    email: 'miriam@example.com',
    password_hash: clientHash,
    role: 'client',
    device: 'iPhone 13',
    platform: 'iOS',
    filter_level: 3,
    status: 'suspended',
    subscription_expires_at: '2026-09-15'
  });

  // בקשות לדוגמה
  const insertRequest = database.prepare(`
    INSERT INTO requests (user_id, type, requested_level, reason, status)
    VALUES (@user_id, @type, @requested_level, @reason, @status)
  `);

  insertRequest.run({ user_id: 2, type: 'level_change', requested_level: 2, reason: 'צריך גישה לתוכן מקצועי', status: 'pending' });
  insertRequest.run({ user_id: 3, type: 'pause', requested_level: null, reason: 'נסיעה לחו"ל', status: 'pending' });
  insertRequest.run({ user_id: 4, type: 'resume', requested_level: null, reason: 'חזרתי מהנסיעה', status: 'approved' });

  // לוגים לדוגמה
  const insertLog = database.prepare(`
    INSERT INTO logs (user_id, action, detail, level)
    VALUES (@user_id, @action, @detail, @level)
  `);

  insertLog.run({ user_id: 1, action: 'system_start', detail: 'המערכת הופעלה', level: 'info' });
  insertLog.run({ user_id: 2, action: 'login', detail: 'כניסה מוצלחת', level: 'info' });
  insertLog.run({ user_id: 3, action: 'filter_triggered', detail: 'תמונה נחסמה', level: 'warning' });
  insertLog.run({ user_id: 4, action: 'status_change', detail: 'סטטוס שונה ל-inactive', level: 'info' });

  // רשימות חסימה DNS לדוגמה
  database.prepare(`
    INSERT INTO dns_blocklists (name, url, enabled)
    VALUES (?, ?, ?)
  `).run('רשימת פורנו - AdGuard', 'https://adguardteam.github.io/AdGuardSDNSFilter/Filters/filter.txt', 1);

  database.prepare(`
    INSERT INTO dns_blocklists (name, url, enabled)
    VALUES (?, ?, ?)
  `).run('רשימת מבוגרים - StevenBlack', 'https://raw.githubusercontent.com/StevenBlack/hosts/master/alternates/porn/hosts', 1);

  console.log('✅ נתוני דוגמה נוספו בהצלחה');
}

module.exports = { getDb, initDatabase };
