# העלאת הגרסה החדשה לשרת — פרומפט מוכן

הגרסה החדשה (ממשק ניהול חדש, הרשמה באפליקציה, חסימות בלחיצה, רמה 2 עם סגמנטציה, תיקוני אליאקספרס וגוגל)
נמצאת ב-GitHub בענף `claude/sweet-heisenberg-tjxlvr`. היא **עוד לא בשרת** — מה שרואים היום ב-`api.filterphone.com` זו הגרסה הקודמת.

רק המחשב שלכם יכול להתחבר לשרת (שם נמצא מפתח ה-SSH), ולכן ההעלאה נעשית משם:

1. פותחים את Claude Code במחשב, **באותה תיקייה של FilterPhone** שעבדתם בה בפעם הקודמת (יש בה תיקייה `secrets`).
2. מעתיקים את כל הבלוק שלמטה ומדביקים.
3. מאשרים לו להריץ פקודות כשהוא מבקש. זה לוקח בערך 20–30 דקות (רובן המתנה לבניית האפליקציה).

אם משהו נכשל, הוא מחזיר את השרת לגרסה הקודמת לבד. לפני כל שינוי נשמר גיבוי מלא, כולל מסד הנתונים.

```text
אתה רץ במחשב שלי, שיש לו גישת SSH לשרת FilterPhone (ssh ubuntu@151.145.88.42).
המשימה: להעלות לשרת את הגרסה החדשה מהענף claude/sweet-heisenberg-tjxlvr במאגר yeled-tov/filterphone,
לפרסם את האפליקציה החדשה, ולוודא שהכול עובד. דבר איתי רק בעברית, בקצרה ובפשטות.
זה שרת חי עם לקוחות: עבוד בזהירות, אל תמחק כלום, ואל תדפיס סיסמאות או טוקנים לפלט.

שלב 0 — בדיקות מקדימות
- ודא שאתה בתיקיית המאגר (יש בה server/, app/, deploy/ ו-secrets/). אם לא — מצא אותה במחשב ועבור אליה.
- git status: אם יש שינויים מקומיים שלא נשמרו — עצור ושאל אותי.
- ssh -o BatchMode=yes ubuntu@151.145.88.42 'uptime; cat /opt/filterphone/src/REVISION; df -h /'
  חייבים לפחות 3GB פנויים. אם החיבור נכשל — עצור ותגיד לי בדיוק מה ראית.
- רשום לעצמך את מספר המכשירים לפני העדכון:
  ssh ubuntu@151.145.88.42 'sudo wg show wg0 peers | wc -l'

שלב 1 — הקוד
- git fetch origin
- git checkout -B deploy-new origin/claude/sweet-heisenberg-tjxlvr
- אם יש פייתון 3.12 במחשב: pip install -e "server[dev]" ואז pytest -q server/tests (צריכות לעבור 98 בדיקות).
  אם אין — דלג (הבדיקות כבר עברו ב-GitHub).

שלב 2 — העלאה לשרת
- bash deploy/deploy.sh 2>&1 | tee deploy-$(date +%Y%m%d-%H%M).log
  הסקריפט מגבה לפני כל שינוי (כולל מסד הנתונים), שומר את הגרסה הקודמת ב-/opt/filterphone/src.prev,
  מעדכן את מסד הנתונים, ומחבר את AdGuard לבד (משתמש API נפרד — המשתמש שלי ב-AdGuard לא משתנה).
- בסוף מודפסת שורה "גיבויים: /root/fp-backups/<תאריך>". שמור את הנתיב — צריך אותו לחזרה לאחור.
- אם הסקריפט נכשל: הראה לי את 40 השורות האחרונות של הלוג, ועבור לשלב "חזרה לאחור".

שלב 3 — בדיקות אחרי ההעלאה (כולן חייבות לעבור)
a. curl -fsS https://api.filterphone.com/health
b. curl -fsS https://api.filterphone.com/v1/catalog | head -c 300    ← צריך להופיע "levels"
c. ssh ubuntu@151.145.88.42 'systemctl is-active fp-engine fp-proxy fp-api adguardhome caddy redis-server wg-quick@wg0; cat /opt/filterphone/src/REVISION; sudo wg show wg0 peers | wc -l'
   כל השירותים active, ומספר המכשירים כמו בשלב 0.
d. ssh ubuntu@151.145.88.42 'sudo journalctl -u fp-api -u fp-engine -n 120 --no-pager | grep -Ei "initial sync|traceback|error|adguard"'
   צריך "initial sync ok", בלי Traceback.
e. ממשק הניהול: הסיסמה בקובץ secrets/ADMIN.txt (שם המשתמש admin).
   POST https://api.filterphone.com/v1/admin/login עם {"username":"admin","password":"..."} → token,
   ואז GET https://api.filterphone.com/v1/admin/overview עם Authorization: Bearer <token>.
   לבדוק: active = מספר המכשירים מקודם, engine = true, adguard.enabled = true ובלי adguard.error.
f. ssh ubuntu@151.145.88.42 'dig +short @127.0.0.1 example.com'    ← מחזיר כתובת IP

חזרה לאחור — רק אם בדיקה נכשלה ואין תיקון בטוח ומהיר:
  ssh ubuntu@151.145.88.42 'sudo bash /opt/filterphone/src/deploy/rollback_version.sh /root/fp-backups/<התאריך משלב 2>'
  אחרי זה חזור על בדיקות a, c, f, ודווח לי מה נכשל ולמה. אל תמשיך לשלבים 4–5.

שלב 4 — מיזוג ל-main (רק אחרי שכל בדיקות שלב 3 עברו)
- git checkout main && git pull --ff-only origin main
- git merge --ff-only origin/claude/sweet-heisenberg-tjxlvr     ← אם זה לא fast-forward: עצור ושאל אותי
- git push origin main
- את הענף feature/customer-intake לא ממזגים: זו גרסה מוקדמת וחלקית של אותו פיצ'ר, והתוכן שלו כבר כלול.

שלב 5 — האפליקציה
- הדחיפה ל-main מפעילה ב-GitHub בנייה של אפליקציה חתומה (כ-10 דקות):
  gh run list -R yeled-tov/filterphone --workflow app.yml --branch main --limit 1
  gh run watch <המספר> -R yeled-tov/filterphone
  (אם gh לא מותקן או לא מחובר — הסבר לי צעד-צעד איך להריץ gh auth login.)
- כשהבנייה הצליחה: bash deploy/publish_apk.sh
- בדיקה: curl -sI https://api.filterphone.com/download/FilterPhone.apk | head -3   ← 200

שלב 6 — סיכום
כתוב לי בעברית, בקצרה: איזו גרסה עלתה, תוצאה של כל בדיקה, נתיב הגיבוי, ואם משהו לא עבר — מה ומה עשית.
ותזכיר לי:
- ממשק הניהול: https://api.filterphone.com/admin (אותו שם משתמש וסיסמה)
- האפליקציה: https://api.filterphone.com/download/ (מתקינים מעל הגרסה הקיימת — הטלפון נשאר מחובר)
```

## מה בודקים אחר כך בעצמכם (5 דקות)

1. **ממשק הניהול:** נכנסים ל-https://api.filterphone.com/admin. התפריט החדש בצד ימין: סקירה, בקשות, מכשירים, לקוחות...
2. **מכשיר:** "מכשירים", לוחצים על הטלפון שלכם, ומדליקים למשל "טיקטוק". אחרי כמה שניות tiktok.com לא נפתח בטלפון.
3. **האפליקציה:** בטלפון נכנסים ל-https://api.filterphone.com/download/, מורידים ומתקינים מעל הקיימת.
4. **הרשמה של לקוח חדש:** בטלפון אחר מתקינים, בוחרים "הצטרפות לשירות" וממלאים. הבקשה מופיעה בממשק תחת "בקשות", ושם מאשרים.
5. **אם המנהל חסם אפליקציות בטלפון** (חנות, דפדפנים, סטטוסים): האפליקציה תבקש להפעיל את "מגן FilterPhone" בהגדרות הנגישות.
