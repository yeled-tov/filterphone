'use strict';
/**
 * פעולות שהבוט מבצע בעצמו במכשיר של הלקוח — בגבולות שהשרת קובע (רשימה סגורה לכל מכשיר):
 *   [[BLOCK:מזהה,מזהה]]  חסימה לצמיתות (הידוק בלבד)
 *   [[OPEN:מזהה:דקות]]   פתיחה זמנית — עד שעה; אחר כך נחסם שוב לבד (גם בטלפון)
 *   [[CLOSE:מזהה]]       סגירת פתיחה זמנית לפני הזמן
 * כל השאר → [[ESCALATE]] לאישור המנהל. כאן רק ההחלטות והטקסטים (טהור, נבדק ביחידה).
 */

/** מספר המכשיר והמזהה מההודעה שהאפליקציה מנסחת ("מכשיר … #16", "מזהה: 7d1ac306b6d02fe3"). */
function deviceHint(text, prev = {}) {
  const t = String(text || '');
  const dev = /מכשיר[^\n#]{0,40}#(\d{1,7})/.exec(t) || /(?:^|\s)#(\d{1,7})(?:\s|$)/.exec(t);
  const aid = /מזהה\s*:?\s*([0-9a-f]{16})\b/i.exec(t);
  return {
    device: dev ? Number(dev[1]) : prev.device || null,
    aid: aid ? aid[1].toLowerCase() : prev.aid || '',
  };
}

/** "play_store:15" → {item, minutes}; דקות לא תקינות → ברירת המחדל; מעל המקסימום → המקסימום. */
function parseOpen(arg, { max = 60, def = 10 } = {}) {
  const [item, mins] = String(arg || '').split(':').map((x) => x.trim());
  const n = Number.parseInt(mins, 10);
  return { item: item || '', minutes: Number.isFinite(n) && n > 0 ? Math.min(n, max) : def };
}

const ilTime = (sec) => new Date(sec * 1000).toLocaleTimeString('he-IL',
  { timeZone: 'Asia/Jerusalem', hour: '2-digit', minute: '2-digit', hour12: false });

/** החלק בהוראות ל-AI: מה מותר לעשות עכשיו במכשיר של הלקוח (או למה אי אפשר). */
function promptSection(crm) {
  if (!crm) {
    return 'הפונה לא זוהה כלקוח. כדי לבצע פעולה במכשיר (חסימה / פתיחה) — בקש/י בנימוס שישלח את ההודעה מאפליקציית FilterPhone (טאב "תמיכה" ← "תמיכה בוואטסאפ"), שם מופיעים מספר המכשיר והמזהה. אל תכתוב/י שביצעת משהו.';
  }
  if (!crm.selected) {
    const list = crm.devices.map((d) => `#${d.device_id} ${d.name || ''}`.trim()).join(', ');
    return `ללקוח כמה מכשירים (${list}). לפני פעולה — שאל/י על איזה מכשיר מדובר (לפי המספר). אל תכתוב/י שביצעת משהו.`;
  }
  const a = crm.actions || { open: [], block: [], opened: [] };
  const fmt = (xs) => xs.map((x) => `${x.id} = ${x.title}`).join('\n');
  const dev = crm.devices.find((d) => d.device_id === crm.selected);
  const opened = (dev?.temp_open || []).map((o) => `${o.item} = ${o.title} (עד ${ilTime(o.until)})`).join('\n');
  return `=== פעולות שאת/ה מבצע/ת בעצמך במכשיר של הלקוח (#${crm.selected}) ===
את/ה *מבצע/ת* את הפעולות האלה מיד (לא "מעביר/ה למערכת") — בתגית בסוף התשובה. המערכת מבצעת ומוסיפה ללקוח שורת אישור עם השעה המדויקת, אז אל תכתוב/י שעה ואל תבטיח/י משהו שלא ברשימה. ללקוח כותבים את השם (מימין ל-=), לא את המזהה.
• פתיחה זמנית — [[OPEN:מזהה:דקות]] (ברירת מחדל ${a.default_minutes || 10} דקות, לכל היותר ${a.max_minutes || 60}). אחרי הזמן נחסם שוב לבד. ביקשו יותר משעה — הסבר/י שהמקסימום הוא שעה והצע/י שעה. אפשר לפתוח:
${fmt(a.open) || '(אין כרגע)'}
• פתוח עכשיו זמנית (לסגירה מוקדמת — [[CLOSE:מזהה]]):
${opened || '(אין)'}
• חסימה לצמיתות — [[BLOCK:מזהה]] (כמה — מופרדים בפסיק). אפשר לחסום:
${fmt(a.block) || '(הכול כבר חסום)'}
• כל דבר אחר (פתיחה קבועה, השהיית סינון, הורדת רמה, משהו שלא ברשימות) — לא מבצעים; כתוב/כתבי שזה עובר לאישור וסיים/י ב-[[ESCALATE:תיאור קצר]].
• מבקשים לחסום משהו שכבר חסום — אמור/אמרי שזה כבר חסום.`;
}

/** שורת האישור ללקוח אחרי פעולה שבוצעה. */
function confirmLine(kind, res) {
  if (kind === 'OPEN') {
    if (res.already_open) return `ℹ️ ${res.title} כבר פתוח.`;
    return `✅ ${res.title} — פתוח עד ${ilTime(res.until)} (${res.minutes} דק'). אחר כך ייחסם שוב אוטומטית.\nאם עדיין חסום — נסו שוב בעוד כמה שניות.`;
  }
  if (kind === 'CLOSE') return res.closed ? `🔒 ${res.title} — נחסם שוב.` : '';
  if (kind === 'BLOCK') {
    return res.titles?.length ? `🔒 נחסם: ${res.titles.join(', ')}.\nהחסימה בטלפון נכנסת לתוקף תוך דקות (מיד אם פותחים את FilterPhone).`
      : 'ℹ️ זה כבר חסום אצלך.';
  }
  return '';
}

module.exports = { deviceHint, parseOpen, promptSection, confirmLine, ilTime };
