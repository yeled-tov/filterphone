// שימוש: node render.js <תיקייה> [fps] — בתיקייה: promo.html (עם src="logo.png" ללוגו), logo.png, fonts.css (Heebo מקומי).
// אחר כך: ffmpeg -framerate 30 -i frames/f%04d.jpg -c:v libx264 -crf 20 -pix_fmt yuv420p -movflags +faststart FilterPhone-promo.mp4
// מצלם את promo.html פריים אחרי פריים (render(t) דטרמיניסטי) — ואז ffmpeg מחבר ל-MP4.
const { chromium } = require('playwright');
const fs = require('fs');
(async () => {
  const dir = process.argv[2];
  const fps = Number(process.argv[3] || 30);
  fs.mkdirSync(`${dir}/frames`, { recursive: true });
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 540, height: 960 }, deviceScaleFactor: 2 });
  await p.goto('file://' + dir + '/promo.html?t=0');
  await p.evaluate(() => document.fonts.ready);
  await p.waitForTimeout(500);
  const total = await p.evaluate(() => window.TOTAL);
  const n = Math.round(total * fps);
  for (let i = 0; i < n; i++) {
    await p.evaluate((t) => window.render(t), i / fps);
    await p.screenshot({ path: `${dir}/frames/f${String(i).padStart(4, '0')}.jpg`, type: 'jpeg', quality: 93 });
  }
  await b.close();
  console.log('frames', n);
})();
