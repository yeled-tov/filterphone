'use strict';
/** שעות הפעילות (שעון ישראל): טווחים בימים א'–ה', שישי עד שעה מסוימת, שבת — סגור. */

function israelNow(date = new Date()) {
  const il = new Date(date.toLocaleString('en-US', { timeZone: 'Asia/Jerusalem' }));
  return { day: il.getDay(), minutes: il.getHours() * 60 + il.getMinutes() };
}

function toMinutes(hhmm) {
  const m = /^(\d{1,2}):(\d{2})$/.exec(String(hhmm || '').trim());
  return m ? Number(m[1]) * 60 + Number(m[2]) : null;
}

/** "13:00-15:00, 20:00-22:30" → [[780,900],[1200,1350]] */
function parseRanges(text) {
  return String(text || '').split(',').map((part) => part.split('-').map(toMinutes))
    .filter(([a, b]) => a !== null && b !== null && b > a);
}

/** בשעות הפעילות? (day: 0=ראשון … 6=שבת) */
function isOpen({ day, minutes }, hoursText, fridayUntil) {
  if (day === 6) return false;                                   // שבת
  if (day === 5) {
    const until = toMinutes(fridayUntil);
    if (until === null || minutes > until) return false;          // שישי אחרי השעה
  }
  return parseRanges(hoursText).some(([a, b]) => minutes >= a && minutes <= b);
}

module.exports = { israelNow, parseRanges, isOpen, toMinutes };
