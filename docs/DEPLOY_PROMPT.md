# העלאת הגרסה החדשה לשרת

הגרסה החדשה (ממשק ניהול חדש, הרשמה באפליקציה, חסימות בלחיצה, רמה 2 עם סגמנטציה, תיקוני אליאקספרס וגוגל)
כבר נמצאת ב-GitHub בענף הראשי (`main`). כדי שתופיע באתר `api.filterphone.com` צריך להעלות אותה לשרת —
ורק המחשב שלכם יכול להתחבר לשרת (שם נמצא מפתח ה-SSH).

## מה עושים (פעם אחת)

1. פותחים את Claude Code במחשב — כמו בפעם הקודמת, באותה תיקייה של FilterPhone.
2. מדביקים את השורה הזו:

```text
עדכן את שרת FilterPhone לגרסה החדשה: עבור לתיקיית המאגר filterphone (אם אין — שכפל מ-https://github.com/yeled-tov/filterphone), הרץ git checkout main && git pull, ואז בצע בדיוק את ההוראות בקובץ docs/DEPLOY_PROMPT.md — הבלוק שמתחיל ב"אתה רץ במחשב שלי".
```

3. מאשרים לו להריץ פקודות כשהוא מבקש. זה לוקח בערך 20 דקות.
4. בסוף הוא כותב לכם את כתובת ממשק הניהול, שם המשתמש והסיסמה.

אם משהו נכשל — הוא מחזיר את השרת לגרסה הקודמת לבד. לפני כל שינוי נשמר גיבוי מלא.
בסוף הוא גם מגדיר "ערוץ העלאה" מ-GitHub, כך שבפעם הבאה Claude יכול לעדכן את השרת בעצמו, בלי המחשב שלכם.

## ההוראות ל-Claude במחשב

```text
אתה רץ במחשב שלי, שיש לו גישת SSH לשרת FilterPhone (ssh ubuntu@151.145.88.42).
המשימה: להעלות לשרת את הגרסה מהענף main במאגר yeled-tov/filterphone, לפרסם את האפליקציה החדשה,
ולוודא שהכול עובד. דבר איתי רק בעברית, בקצרה ובפשטות.
זה שרת חי: עבוד בזהירות, ואל תמחק כלום. סיסמאות וטוקנים — לא לקבצי לוג ולא ל-git.

שלב 0 — בדיקות מקדימות
- ודא שאתה בתיקיית המאגר (יש בה server/, app/, deploy/). אם לא — מצא אותה, או שכפל את המאגר לתיקייה חדשה.
- git status: אם יש שינויים מקומיים שלא נשמרו — עצור ושאל אותי.
- git checkout main && git pull --ff-only origin main
- ssh -o BatchMode=yes ubuntu@151.145.88.42 'uptime; cat /opt/filterphone/src/REVISION; df -h /'
  חייבים לפחות 3GB פנויים. אם החיבור נכשל — עצור ותגיד לי בדיוק מה ראית.
- רשום לעצמך את מספר המכשירים לפני העדכון:
  ssh ubuntu@151.145.88.42 'sudo wg show wg0 peers | wc -l'

שלב 1 — העלאה לשרת
- bash deploy/deploy.sh 2>&1 | tee deploy-$(date +%Y%m%d-%H%M).log
  הסקריפט מגבה לפני כל שינוי (כולל מסד הנתונים), שומר את הגרסה הקודמת ב-/opt/filterphone/src.prev,
  מעדכן את מסד הנתונים, ומחבר את AdGuard לבד (משתמש API נפרד — המשתמש שלי ב-AdGuard לא משתנה).
- בסוף מודפסת שורה "גיבויים: /root/fp-backups/<תאריך>". שמור את הנתיב — צריך אותו לחזרה לאחור.
- אם הסקריפט נכשל: הראה לי את 40 השורות האחרונות של הלוג, ועבור ל"חזרה לאחור".

שלב 2 — בדיקות אחרי ההעלאה (כולן חייבות לעבור)
a. curl -fsS https://api.filterphone.com/health
b. curl -fsS https://api.filterphone.com/v1/catalog | head -c 300    ← צריך להופיע "levels"
c. ssh ubuntu@151.145.88.42 'systemctl is-active fp-engine fp-proxy fp-api adguardhome caddy redis-server wg-quick@wg0; cat /opt/filterphone/src/REVISION; sudo wg show wg0 peers | wc -l'
   כל השירותים active, ומספר המכשירים כמו בשלב 0.
d. ssh ubuntu@151.145.88.42 'sudo journalctl -u fp-api -u fp-engine -n 120 --no-pager | grep -Ei "initial sync|traceback|error|adguard"'
   צריך "initial sync ok", בלי Traceback.
e. ssh ubuntu@151.145.88.42 'dig +short @127.0.0.1 example.com'    ← מחזיר כתובת IP
f. ssh ubuntu@151.145.88.42 'curl -fsS http://127.0.0.1:8090/ready; echo; curl -fsS http://127.0.0.1:8090/metrics | grep -c "^fp_"'
   צריך "ready": true (וב-checks: db, redis, engine, wireguard — true), ומספר מדדים גדול מ-0.
   (אם engine=false — לחכות דקה ולבדוק שוב: המנוע טוען מודלים בעלייה.)
g. ssh ubuntu@151.145.88.42 'grep -c "^FP_PUBLIC_IP=" /etc/filterphone/filterphone.env'   ← 1
   (ההתקנה מוסיפה לבד את כתובת השרת — גיבוי לטלפונים כשה-DNS לא זמין.)
h. ssh ubuntu@151.145.88.42 'curl -s https://www.google.com -o /dev/null -w "%{http_code}\n"'   ← 200

חזרה לאחור — רק אם בדיקה נכשלה ואין תיקון בטוח ומהיר:
  ssh ubuntu@151.145.88.42 'sudo bash /opt/filterphone/src/deploy/rollback_version.sh /root/fp-backups/<התאריך משלב 1>'
  אחרי זה חזור על בדיקות a, c, e, ודווח לי מה נכשל ולמה. אל תמשיך לשלבים הבאים.

שלב 3 — הכניסה לממשק הניהול
- הסיסמה שמורה בקובץ secrets/ADMIN.txt בתיקיית המאגר (שם המשתמש: admin).
- בדוק שהיא עובדת: POST https://api.filterphone.com/v1/admin/login עם {"username":"admin","password":"..."} → token,
  ואז GET https://api.filterphone.com/v1/admin/overview עם Authorization: Bearer <token>.
  לבדוק: active = מספר המכשירים מקודם, engine = true, adguard.enabled = true ובלי adguard.error.
- אם אין קובץ כזה, או שהכניסה נכשלת — קבע סיסמה חדשה (לפחות 16 תווים, אקראית):
  printf '%s\n' "<הסיסמה>" | ssh ubuntu@151.145.88.42 'sudo -u filterphone /opt/filterphone/venv/bin/python -m filterphone.api.cli set-admin admin'
  שמור אותה ב-secrets/ADMIN.txt (את הישנה, אם יש — ב-secrets/ADMIN.old.txt), ובדוק שוב את הכניסה.

שלב 4 — האפליקציה
- הבנייה של האפליקציה החתומה ב-GitHub רצה לבד על main. ודא שהיא הסתיימה בהצלחה:
  gh run list -R yeled-tov/filterphone --workflow app.yml --branch main --limit 1
  (אם היא עוד רצה: gh run watch <המספר> -R yeled-tov/filterphone.
   אם gh לא מותקן או לא מחובר — הסבר לי צעד-צעד איך להריץ gh auth login.)
- bash deploy/publish_apk.sh
- בדיקה: curl -sI https://api.filterphone.com/download/FilterPhone.apk | head -3   ← 200
- בדיקה: curl -fsS https://api.filterphone.com/download/FilterPhone.json | head -c 300   ← "version_name" ו-"files"
  (ממנו השרת מציע עדכון לטלפונים: במנהל מכשיר — עדכון שקט אחרי בדיקת טביעה וחתימה; בשאר — הודעה באפליקציה.)
- טביעת החתימה להקמת מכשיר מנוהל ב-QR (חד-פעמי, לא חובה אבל מומלץ):
  מצא בלוג של הבנייה האחרונה (gh run view <המספר> -R yeled-tov/filterphone --log | grep "certificate SHA-256")
  את 64 התווים, ואם בשרת אין עדיין FP_APK_SIGNATURE_SHA256 — הוסף:
  ssh ubuntu@151.145.88.42 'grep -q "^FP_APK_SIGNATURE_SHA256=" /etc/filterphone/filterphone.env || echo "FP_APK_SIGNATURE_SHA256=<64 התווים>" | sudo tee -a /etc/filterphone/filterphone.env >/dev/null; sudo systemctl restart fp-api'
  ובדוק שוב את בדיקה a.

שלב 5 — ערוץ העלאה מ-GitHub (חד-פעמי, כדי ש-Claude בענן יוכל לעדכן את השרת בעצמו בפעם הבאה)
מפתח SSH נפרד, שבשרת מוגבל להרצת /usr/local/sbin/fp-ci-deploy בלבד (העלאה / פרסום אפליקציה / מצב / חזרה לאחור) —
בלי shell ובלי גישה לשום דבר אחר. עשה את זה בתיקייה זמנית מחוץ למאגר:
- ssh-keygen -t ed25519 -N "" -C fp-ci-deploy -f fp_ci_key
- הוספת המפתח לשרת (עם גיבוי של הקובץ, בלי לגעת במפתח שלי):
  printf 'restrict,command="/usr/local/sbin/fp-ci-deploy" %s\n' "$(cat fp_ci_key.pub)" | ssh ubuntu@151.145.88.42 'f=~/.ssh/authorized_keys; cp "$f" "$f.bak-fpci"; grep -v " fp-ci-deploy$" "$f.bak-fpci" > "$f.new"; cat >> "$f.new"; chmod 600 "$f.new"; mv "$f.new" "$f"; [ "$(grep -vc " fp-ci-deploy$" "$f.bak-fpci")" = "$(grep -vc " fp-ci-deploy$" "$f")" ] && echo OK || { cp "$f.bak-fpci" "$f"; echo RESTORED; }'
  חייב להדפיס OK (המפתחות הקיימים נשארו כמו שהם). אם הודפס RESTORED — הקובץ הוחזר למצבו הקודם; עצור ודווח לי.
- ודא שהחיבור הרגיל שלי עדיין עובד: ssh -o BatchMode=yes ubuntu@151.145.88.42 true
- בדיקה: ssh -i fp_ci_key -o IdentitiesOnly=yes ubuntu@151.145.88.42 status   ← מדפיס גרסה ושירותים
  ו-ssh -i fp_ci_key -o IdentitiesOnly=yes ubuntu@151.145.88.42 id         ← חייב להיענות "פקודה לא מוכרת"
- gh secret set FP_DEPLOY_KEY -R yeled-tov/filterphone < fp_ci_key
- מפתח השרת לאימות: ssh-keygen -F 151.145.88.42 | grep -v '^#' | gh secret set FP_DEPLOY_KNOWN_HOSTS -R yeled-tov/filterphone
  (אם אין שורה כזו: ssh-keyscan -t ed25519 151.145.88.42, ולוודא שטביעת האצבע זהה ל-
   ssh ubuntu@151.145.88.42 'ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub' לפני ששומרים)
- מחק את fp_ci_key ו-fp_ci_key.pub מהמחשב.
- בדיקה מלאה: gh workflow run deploy.yml -R yeled-tov/filterphone -f what=status ואז gh run watch — צריך להצליח.

שלב 6 — סיכום
כתוב לי בעברית, בקצרה: איזו גרסה עלתה, תוצאה של כל בדיקה, נתיב הגיבוי, ואם משהו לא עבר — מה ומה עשית.
ובסוף, בשורות נפרדות וברורות:
- ממשק הניהול: https://api.filterphone.com/admin
- שם משתמש: admin
- סיסמה: <הסיסמה מ-secrets/ADMIN.txt>
- האפליקציה לטלפון: https://api.filterphone.com/download/ (מתקינים מעל הגרסה הקיימת)
```

## מה בודקים אחר כך בעצמכם (5 דקות)

1. **ממשק הניהול:** נכנסים ל-https://api.filterphone.com/admin. בצד ימין יש תפריט חדש: סקירה, בקשות, מכשירים, לקוחות...
2. **מכשיר:** "מכשירים", לוחצים על הטלפון שלכם, ומדליקים למשל "טיקטוק". אחרי כמה שניות tiktok.com לא נפתח בטלפון.
3. **האפליקציה:** בטלפון נכנסים ל-https://api.filterphone.com/download/, מורידים ומתקינים מעל הקיימת.
4. **הרשמה של לקוח חדש:** בטלפון אחר מתקינים, בוחרים "הצטרפות לשירות" וממלאים. הבקשה מופיעה בממשק תחת "בקשות", ושם מאשרים.
5. **אם המנהל חסם אפליקציות בטלפון** (חנות, דפדפנים, סטטוסים): האפליקציה תבקש להפעיל את "מגן FilterPhone" בהגדרות הנגישות.
6. **מצב ההגנה:** במסך הראשי של האפליקציה מופיעים "סוללה", "מגן", "סנכרון אחרון". אם כתוב "סוללה: מוגבלת" — לוחצים ומאשרים "ללא הגבלה". בממשק, ברשימת המכשירים, כל מכשיר ירוק / צהוב / אדום עם הסיבה.
7. **מכשיר מנוהל (Device Owner) — לא חובה:** טלפון מאופס ← בממשק "קודים" ← "הקמת מכשיר מנוהל ב-QR". הסבר מלא, כולל מה אנדרואיד לא מאפשר: [DEVICE_OWNER.md](DEVICE_OWNER.md).
