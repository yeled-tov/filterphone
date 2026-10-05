# FilterPhone

סינון אינטרנט לכל הטלפון: **VPN** (WireGuard) + **סינון DNS** (AdGuard Home) + **סינון תמונות בצד השרת** (SigLIP2 + NudeNet), עם אפליקציה אחת ב-Flutter.

```
טלפון (אפליקציית FilterPhone)
  │  WireGuard — כל התעבורה, IPv4 ו-IPv6
  ▼
שרת (Oracle, ירושלים)
  ├─ DNS ──────────────▶ AdGuard Home: חסימת אתרים, חיפוש בטוח, חסימת עקיפות
  ├─ HTTP/HTTPS ───────▶ fp-proxy (mitmproxy שקוף)
  │                         │  רק תמונות נאספות; כל השאר זורם ישר
  │                         ▼
  │                      fp-engine: hash → קאש (Redis) → SigLIP2 + ראשים → [NudeNet] → החלטה
  └─ api.filterphone.com ▶ Caddy (HTTPS) → fp-api: הרשמת מכשירים, רמות, ממשק ניהול
```

## מבנה המאגר

| תיקייה | מה יש בה |
|---|---|
| [`server/`](server/) | קוד השרת (Python): `engine` — מנוע התמונות, `proxy` — תוסף mitmproxy, `api` — API וממשק ניהול. 42 בדיקות |
| [`app/`](app/) | האפליקציה (Flutter). אנדרואיד מלא; אייפון — המבנה מוכן, חסר Network Extension |
| [`deploy/`](deploy/) | התקנה לשרת: `install.sh`, חומת אש, systemd, Caddy, AdGuard, חזרה לאחור |
| [`tools/`](tools/) | אימון ראשי הסיווג (רץ ב-GitHub), הורדת מודלים עם אימות חתימה, בדיקת עשן |
| [`docs/`](docs/) | [ניתוח השרת הישן](docs/ANALYSIS.md), [דוח דיוק הראשים](docs/HEADS_REPORT.md) |
| [`legacy/`](legacy/) | העתק של המערכת הישנה (לפני 5.10.2026), בלי סודות — לעיון בלבד |
| [`ops/`](ops/) | שומר דיסק ותקרת לוגים (מותקנים בשרת) |

## רמות סינון

| רמה | שם | מה נחסם |
|---|---|---|
| 0 | כבוי | שום תמונה (DNS עדיין מסנן) |
| 1 | מחמיר | כל תמונה של אישה/ילדה, גם בלבוש צנוע. גבר — עובר |
| 2 | בינוני | נשים בלבוש צנוע עוברות; אזורים חשופים נצבעים בשחור; עירום נחסם |
| 3 | בסיסי | רק עירום מפורש |

## הפעלה

### שרת
```bash
deploy/deploy.sh
```
מעלה את `server/`, `tools/` ו-`deploy/` לשרת ומריץ `install.sh`. הסקריפט בטוח להרצה חוזרת, ומגבה לפני כל שינוי ל-`/root/fp-backups/<תאריך>`. לחזרה למערכת הישנה: `sudo bash /opt/filterphone/src/deploy/rollback.sh /root/fp-backups/<תאריך>`.

מנהל ראשון (פעם אחת, בשרת):
```bash
sudo -u filterphone /opt/filterphone/venv/bin/python -m filterphone.api.cli set-admin admin
```

### אפליקציה
כל דחיפה ל-`app/` בונה ב-GitHub Actions קובץ APK חתום (Artifacts → `FilterPhone-APK`). לפרסום לדף ההורדה:
```bash
deploy/publish_apk.sh
```
הלקוחות מורידים מ-`https://api.filterphone.com/download/`.

### ראשי הסיווג
GitHub → Actions → "אימון ראשי סיווג" → Run. האימון שומר `heads.json` ודוח דיוק במאגר; אחר כך `deploy/deploy.sh`.

## עבודה יומיומית

1. **ממשק ניהול:** `https://api.filterphone.com/admin`
2. **לקוח חדש:** "קודי הפעלה" ← שם הלקוח + רמה ← "צור קוד" ← שולחים ללקוח את הקוד ואת קישור ההורדה.
3. **שינוי רמה / השבתה / מחיקה:** בלשונית "מכשירים". השינוי חל תוך כמה שניות.
4. **אפליקציה שלא עובדת אצל לקוח:** "איפוס אפליקציות" מוחק את רשימת האפליקציות שהמערכת למדה להעביר בלי בדיקה.

## בדיקות ותקלות (בשרת)

```bash
systemctl status fp-engine fp-proxy fp-api caddy adguardhome
journalctl -u fp-engine -n 50     # סיכום כל 5 דקות: כמה תמונות, כמה מהקאש, כמה נחסמו
journalctl -u fp-proxy -n 50
```

## מגבלות שחשוב להכיר

- **אנדרואיד 7+:** רוב האפליקציות לא סומכות על תעודה שהמשתמש התקין, ולכן סינון התמונות חל על **Chrome** ודפדפנים דומים. אפליקציות אחרות עוברות אוטומטית בלי פתיחת HTTPS (סינון DNS ממשיך לחול). ברמה 1, שרתי התמונות של הרשתות החברתיות לא נפתחים, כך שהתמונות שם פשוט לא נטענות.
- **אייפון:** דורש חשבון Apple Developer בתשלום (Network Extension) ובנייה על macOS.
- **רישיונות:** SigLIP2 — Apache-2.0. ‏WireGuard — Apache-2.0 / GPL-2.0. ‏**NudeNet — AGPL-3.0**: בשימוש כשירות רשת כדאי לבדוק משפטית, או להחליף בעתיד בגלאי ברישיון מתירני.

## שרת

- `151.145.88.42` (Oracle Cloud, ירושלים, ARM 4 ליבות / 24GB)
- גישה: `ssh ubuntu@151.145.88.42`
