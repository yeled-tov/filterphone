# שרת מרכזי - FilterPhone

שרת Backend למערכת סינון תוכן FilterPhone.

## טכנולוגיות
- Node.js + Express
- SQLite (better-sqlite3)
- JWT לאימות
- bcrypt להצפנת סיסמאות

## התקנה והרצה

```bash
npm install
npm start
```

השרת יעלה על פורט **3001**.

## פרטי כניסה ראשוניים

| תפקיד | אימייל | סיסמה |
|-------|--------|-------|
| מנהל  | admin@filterphone.com | REDACTED |
| לקוח  | israel@example.com | REDACTED |
| לקוח  | sarah@example.com | REDACTED |
| לקוח  | david@example.com | REDACTED |

## API Endpoints

### אימות
- `POST /api/auth/login` - התחברות
- `POST /api/auth/register` - רישום (לאפליקציה בעתיד)
- `GET /api/auth/me` - פרטי המשתמש המחובר

### משתמשים (מנהל בלבד)
- `GET /api/users` - כל המשתמשים
- `POST /api/users` - יצירת משתמש
- `PUT /api/users/:id` - עדכון משתמש
- `DELETE /api/users/:id` - מחיקת משתמש
- `PUT /api/users/:id/filter-level` - שינוי רמת סינון
- `PUT /api/users/:id/status` - שינוי סטטוס

### בקשות
- `POST /api/requests` - שליחת בקשה (לקוח)
- `GET /api/requests` - כל הבקשות (מנהל) / בקשות שלי (לקוח)
- `PUT /api/requests/:id/approve` - אישור בקשה (מנהל)
- `PUT /api/requests/:id/reject` - דחיית בקשה (מנהל)

### לוגים וסטטיסטיקות
- `GET /api/logs` - לוגים (מנהל)
- `GET /api/stats` - סטטיסטיקות (מנהל)

## רמות סינון
1. **רמה 1** - חסימה מלאה
2. **רמה 2** - טשטוש חלקי
3. **רמה 3** - צניעות בלבד
