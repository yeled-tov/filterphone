# בוט וואטסאפ לא-רשמי ל-FilterPhone

גשר שמחבר את **הוואטסאפ הרגיל שלך** ל-FilterPhone: עונה ללקוחות כשאינך זמין, מעביר אליך פניות
מהאפליקציה, ומאפשר לך לענות ללקוח ישירות מהוואטסאפ. מבוסס `whatsapp-web.js`.

> ⚠️ **אזהרה חשובה:** זהו API **לא-רשמי** של וואטסאפ ונוגד את תנאי השימוש. יש **סיכון אמיתי
> שהמספר ייחסם** (זמנית או לצמיתות). הבוט תוכנן להתנהג כמו קו שירות אמיתי — מגיב בלבד, קצב סביר,
> ומוסר אליך (handoff). אין בו ואין להוסיף לו מנגנונים "להתחמק" מזיהוי. השימוש באחריותך.

## מה הוא עושה

- **פניות מהאפליקציה → אליך:** כל הודעה שלקוח כותב בצ'אט התמיכה באפליקציה מגיעה כהודעה
  בצ'אט-עם-עצמך בוואטסאפ שלך.
- **אתה עונה → ללקוח:** "השב" (reply) על אותה הודעה, או כתוב `#<מספר מכשיר> טקסט` — והתשובה מגיעה
  ללקוח באפליקציה.
- **מענה אוטומטי אנושי:** אנשים שכותבים לוואטסאפ שלך (לקוחות/קונים) מקבלים מענה חכם ואנושי — אבל
  **רק כשאינך פעיל** באותה שיחה. ברגע שאתה כותב שם בעצמך, הבוט שותק שם (handoff) ל-20 דקות.
- **CRM ופעולות מדורגות:** הבוט מזהה לקוח לפי הטלפון. פעולה **בטוחה** (חסימה) מתבצעת לבד; פעולה
  **קריטית** (השהיה/פתיחה/הורדת רמה) רק **נרשמת לאישורך** — הבוט לא מבצע אותה.

## התקנה (פעם אחת)

1. **טוקן בוט:** בדשבורד → מערכת → תמיכה בוואטסאפ → "בוט וואטסאפ לא-רשמי" → יצירה → שמירה.
   העתק את הטוקן.
2. בשרת (או כל מחשב שרץ תמיד), בתיקייה הזו:
   ```bash
   npm install
   ```
   (צריך Node 18+ ו-Chromium. אם אין Chromium מותקן, התקן: `sudo apt-get install -y chromium`,
   והצבע עליו ב-`CHROME_PATH`.)
3. הגדר משתני סביבה (או קובץ `.env` עם `export`):
   ```bash
   export FP_API=https://api.filterphone.com
   export FP_BOT_TOKEN=<הטוקן מהדשבורד>
   export GROQ_API_KEY=<אופציונלי — למענה חכם; מפתח חינם מ-console.groq.com>
   export CHROME_PATH=/usr/bin/chromium        # אם צריך
   ```
4. הפעל וסרוק QR (פעם אחת):
   ```bash
   npm start
   ```
   מהוואטסאפ שלך: הגדרות → מכשירים מקושרים → קישור מכשיר → סרוק את ה-QR שמופיע בטרמינל.
   אחרי הסריקה, החיבור נשמר (תיקיית `session/`) ולא צריך לסרוק שוב.

## הרצה קבועה (systemd)

`/etc/systemd/system/fp-wabot.service`:
```ini
[Unit]
Description=FilterPhone WhatsApp bot
After=network-online.target

[Service]
WorkingDirectory=/opt/filterphone-wabot
Environment=FP_API=https://api.filterphone.com
Environment=FP_BOT_TOKEN=xxxxx
Environment=GROQ_API_KEY=xxxxx
Environment=CHROME_PATH=/usr/bin/chromium
ExecStart=/usr/bin/node index.js
Restart=always
RestartSec=10
User=wabot

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl enable --now fp-wabot
sudo journalctl -u fp-wabot -f     # לראות את ה-QR בהרצה הראשונה
```

## משתני סביבה

| משתנה | ברירת מחדל | מה |
|---|---|---|
| `FP_API` | https://api.filterphone.com | כתובת שרת FilterPhone |
| `FP_BOT_TOKEN` | — (חובה) | הטוקן מהדשבורד |
| `GROQ_API_KEY` | — | מענה חכם (בלי זה: מענה בסיסי + handoff) |
| `GROQ_MODEL` | llama-3.3-70b-versatile | מודל השפה |
| `FP_POLL_MS` | 5000 | כל כמה זמן בודק פניות חדשות |
| `FP_ADMIN_ACTIVE_MS` | 1200000 | כמה זמן הבוט שותק בשיחה אחרי שענית (20 דק') |
| `CHROME_PATH` | אוטומטי | נתיב ל-Chromium אם צריך |
| `FP_SESSION_DIR` | ./session | איפה נשמר החיבור |

## פרטיות

הבוט לא שומר תוכן הודעות אצלו מעבר להיסטוריה קצרה בזיכרון (לצורך ההקשר לשיחה). הפניות עצמן
נשמרות בשרת FilterPhone כמו כל פנייה בצ'אט התמיכה.
