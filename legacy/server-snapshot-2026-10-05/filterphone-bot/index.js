require('dotenv').config();
const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');
const Groq = require('groq-sdk');
const Database = require('better-sqlite3');

const ADMIN_NUMBER = '972527186881@c.us';
const ADMIN_LID    = '178138734674132@lid';

function isAdminId(id) {
    return id === ADMIN_NUMBER || id === ADMIN_LID;
}

const BUSINESS = {
    address: 'חטיבת גבעת שתיים, אשדוד',
    waze: 'https://waze.com/ul?q=' + encodeURIComponent('חטיבת גבעת שתיים אשדוד') + '&navigate=yes',
    maps: 'https://www.google.com/maps/search/?api=1&query=' + encodeURIComponent('חטיבת גבעת שתיים אשדוד'),
    hours: '13:00-15:00 ו-20:00-22:30 (לא בשבת)'
};

// ========== הגדרות זמן ==========
const DELAY_AVAILABLE   = 5000;
const DELAY_UNAVAILABLE = 0;
const READ_CHECK_WAIT   = 3000;
const ADMIN_ACTIVE_MS   = 120000;

// ========== Prompts ==========
const PROMPT_HOURS = `אתה "פילטר", נציג שירות לקוחות של FilterPhone - חברה ישראלית מאשדוד לסינון תוכן.
התכתב בעברית טבעית, חמה - כמו בן אדם. משפטים קצרים. אל תחשוף שאתה AI.
כשמתאים הזכר את האתר: filterphone.com
אם שואלים על כתובת/הגעה - כתוב: ADDRESS
אם הלקוח רוצה נציג/התקנה/מחיר מדויק - כתוב: HANDOFF

מידע:
• אייפון: חסימת אתרים/פרסומות/רשתות חברתיות/ספציפיים. אפשר לחסום גוגל ואפסטור. אין יוטיוב כשר.
• אנדרואיד:
  1. כשר פלאי: צריבה 70₪, מנוי 150₪ לחיים/לשנה עם שירות+חנות, דפדפן +80₪/שנה
  2. עסקן (גלקסי בלבד): צריבה 300₪, מנוי 40₪/חודש לשנה וחצי
  3. הדרן (הכי חזק, עלול לפגוע במכשיר): צריבה 300₪, מנוי 40₪/חודש לשנה וחצי
בהודעה ראשונה - ברך ושאל "במה אפשר לעזור?" בלבד.`;

const PROMPT_OFF = `אתה העוזר האוטומטי של FilterPhone.
פתח כל שיחה ראשונה כך:
"שלום! אני העוזר האוטומטי של FilterPhone 🤖
כעת מחוץ לשעות הפעילות.
שעות: 13:00-15:00 ו-20:00-22:30 (לא בשבת)
נציג יחזור אליך בהקדם. במה אפשר לעזור?"
אם שואלים כתובת - כתוב: ADDRESS
אם רוצים נציג/מחיר/התקנה - כתוב: HANDOFF
מידע בסיסי בלבד. אל תמציא.`;

// ========== DB ==========
const db = new Database('/home/ubuntu/filterphone-bot/filterphone.db');
db.exec(`
    CREATE TABLE IF NOT EXISTS conversations (
        user_id TEXT PRIMARY KEY,
        real_phone TEXT,
        handed_off INTEGER DEFAULT 0,
        bot_paused INTEGER DEFAULT 0,
        admin_intervened INTEGER DEFAULT 0,
        last_message_at INTEGER
    );
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT, direction TEXT, message TEXT, timestamp INTEGER
    );
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY, value TEXT
    );
    CREATE TABLE IF NOT EXISTS admin_state (
        admin_id TEXT PRIMARY KEY,
        menu_open INTEGER DEFAULT 0,
        target_user TEXT,
        last_action_at INTEGER
    );
`);

const stmts = {
    getConv:      db.prepare('SELECT * FROM conversations WHERE user_id = ?'),
    upsertConv:   db.prepare(`INSERT INTO conversations (user_id,real_phone,handed_off,bot_paused,admin_intervened,last_message_at)
                              VALUES (?,?,?,?,?,?)
                              ON CONFLICT(user_id) DO UPDATE SET
                                real_phone=COALESCE(excluded.real_phone,real_phone),
                                handed_off=excluded.handed_off,
                                bot_paused=excluded.bot_paused,
                                admin_intervened=excluded.admin_intervened,
                                last_message_at=excluded.last_message_at`),
    pauseConv:    db.prepare('UPDATE conversations SET bot_paused=?, admin_intervened=? WHERE user_id=?'),
    getMsgs:      db.prepare('SELECT direction,message FROM messages WHERE user_id=? ORDER BY timestamp ASC LIMIT 30'),
    logMsg:       db.prepare('INSERT INTO messages (user_id,direction,message,timestamp) VALUES (?,?,?,?)'),
    getAllConvs:  db.prepare('SELECT user_id,real_phone,handed_off,bot_paused,admin_intervened,last_message_at FROM conversations ORDER BY last_message_at DESC LIMIT 10'),
    getByPhone:   db.prepare('SELECT * FROM conversations WHERE real_phone=?'),
    getSetting:   db.prepare('SELECT value FROM settings WHERE key=?'),
    setSetting:   db.prepare('INSERT INTO settings (key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value'),
    getAdminState:db.prepare('SELECT * FROM admin_state WHERE admin_id=?'),
    setAdminState:db.prepare(`INSERT INTO admin_state (admin_id,menu_open,target_user,last_action_at)
                              VALUES (?,?,?,?)
                              ON CONFLICT(admin_id) DO UPDATE SET
                                menu_open=excluded.menu_open,
                                target_user=excluded.target_user,
                                last_action_at=excluded.last_action_at`)
};

function getBotActive() {
    const r = stmts.getSetting.get('bot_active');
    return r ? r.value === '1' : true;
}
function setBotActive(v) { stmts.setSetting.run('bot_active', v ? '1' : '0'); }

// ========== Groq ==========
const groq = new Groq({ apiKey: process.env.GROQ_API_KEY });

function isBusinessHours() {
    const il = new Date(new Date().toLocaleString('en-US', { timeZone: 'Asia/Jerusalem' }));
    const day = il.getDay();
    if (day === 6) return false;
    const cur = il.getHours() * 60 + il.getMinutes();
    if (day === 5 && cur > 720) return false;
    return (cur >= 780 && cur <= 900) || (cur >= 1200 && cur <= 1350);
}

async function getAIResponse(userId, userMessage) {
    const history = stmts.getMsgs.all(userId);
    const completion = await groq.chat.completions.create({
        model: 'llama-3.3-70b-versatile',
        max_tokens: 500,
        messages: [
            { role: 'system', content: isBusinessHours() ? PROMPT_HOURS : PROMPT_OFF },
            ...history.map(m => ({ role: m.direction === 'in' ? 'user' : 'assistant', content: m.message })),
            { role: 'user', content: userMessage }
        ]
    });
    return completion.choices[0].message.content;
}

// ========== עזרים ==========
function timeAgo(ts) {
    if (!ts) return '?';
    const d = Math.floor((Date.now() - ts) / 60000);
    if (d < 1) return 'עכשיו';
    if (d < 60) return `${d} דק'`;
    const h = Math.floor(d / 60);
    return h < 24 ? `${h} שע'` : `${Math.floor(h/24)} ימים`;
}

async function getRealPhone(message) {
    try {
        const contact = await message.getContact();
        if (contact?.number && /^\d{7,}$/.test(contact.number)) return contact.number;
        if (contact?.id?.user && /^\d{7,}$/.test(contact.id.user)) return contact.id.user;
    } catch (e) {}
    const raw = message.from.replace(/@.+/, '');
    return raw;
}

function addressMsg() {
    return `📍 *הכתובת שלנו:*\n${BUSINESS.address}\n\n🚗 Waze:\n${BUSINESS.waze}\n\n🗺️ Google Maps:\n${BUSINESS.maps}`;
}

// ========== זיהוי זמינות ==========
let adminLastSeen = 0;

async function checkAdminRead(msg, waitMs) {
    return new Promise(resolve => {
        setTimeout(async () => {
            try {
                const chat = await msg.getChat();
                const msgs = await chat.fetchMessages({ limit: 5 });
                const m = msgs.find(x => x.id._serialized === msg.id._serialized);
                resolve(m ? m.ack >= 3 : false);
            } catch { resolve(false); }
        }, waitMs);
    });
}

// ========== הודעות ממתינות ==========
const pending = new Map();

function cancelPending(uid) {
    const p = pending.get(uid);
    if (p) { clearTimeout(p); pending.delete(uid); return true; }
    return false;
}

function scheduleReply(uid, delayMs, cb) {
    cancelPending(uid);
    if (delayMs <= 0) { cb().catch(console.error); return; }
    pending.set(uid, setTimeout(() => { pending.delete(uid); cb().catch(console.error); }, delayMs));
}

// ========== מניעת לולאה - IDs של הודעות שהבוט שלח ==========
const botMsgIds = new Set();

// ========== תפריט אדמין ==========
const MENU = `🛠️ *תפריט ניהול הבוט*

*1* - 🔴 עצור הבוט לכולם
*2* - 🟢 הפעל הבוט לכולם
*3* - 📊 סטטוס ושיחות
*4* - ⏸️ עצור לקוח ספציפי
*5* - ▶️ הפעל לקוח ספציפי
*6* - 🔄 איפוס היסטוריה
*7* - 💬 שלח הודעה ללקוח

*0* - ❌ סגור`;

const PHONE_REQ = `📞 שלח מספר טלפון של הלקוח (לדוגמה: 972501234567)\nאו *0* לחזרה`;
const MSG_REQ = (p) => `💬 שלח הודעה ל-${p}\nאו *0* לחזרה`;

async function safeAdminSend(text) {
    const m = await client.sendMessage(ADMIN_NUMBER, text);
    if (m?.id?._serialized) botMsgIds.add(m.id._serialized);
    return m;
}

async function handleAdmin(message, state) {
    const text = message.body.trim();
    const now = Date.now();
    adminLastSeen = now;

    if (text === 'בוט' || text.toLowerCase() === 'bot') {
        stmts.setAdminState.run(ADMIN_NUMBER, 1, null, now);
        await safeAdminSend(MENU);
        return true;
    }

    if (!state?.menu_open) return false;

    if (text === '0') {
        if (state.target_user) {
            stmts.setAdminState.run(ADMIN_NUMBER, 1, null, now);
            await safeAdminSend(MENU);
        } else {
            stmts.setAdminState.run(ADMIN_NUMBER, 0, null, now);
            await safeAdminSend('✅ התפריט נסגר.');
        }
        return true;
    }

    // שלב עם מספר טלפון
    if (state.target_user?.startsWith('action:')) {
        const action = state.target_user.replace('action:', '');
        const phone = text.replace(/\D/g, '');
        if (!phone || phone.length < 8) {
            await safeAdminSend('⚠️ מספר לא תקין. נסה שוב או שלח *0*.');
            return true;
        }
        let uid = phone + '@c.us';
        const byPhone = stmts.getByPhone.get(phone);
        if (byPhone) uid = byPhone.user_id;

        if (action === 'pause') {
            stmts.pauseConv.run(1, 1, uid);
            stmts.setAdminState.run(ADMIN_NUMBER, 0, null, now);
            await safeAdminSend(`🔴 הבוט עצור עבור ${phone}.`);
            return true;
        }
        if (action === 'resume') {
            stmts.pauseConv.run(0, 0, uid);
            stmts.setAdminState.run(ADMIN_NUMBER, 0, null, now);
            await safeAdminSend(`🟢 הבוט פעיל שוב עבור ${phone}.`);
            return true;
        }
        if (action === 'reset') {
            db.prepare('DELETE FROM messages WHERE user_id=?').run(uid);
            db.prepare('DELETE FROM conversations WHERE user_id=?').run(uid);
            stmts.setAdminState.run(ADMIN_NUMBER, 0, null, now);
            await safeAdminSend(`🔄 היסטוריה של ${phone} נמחקה.`);
            return true;
        }
        if (action === 'msg') {
            stmts.setAdminState.run(ADMIN_NUMBER, 1, `send:${uid}:${phone}`, now);
            await safeAdminSend(MSG_REQ(phone));
            return true;
        }
    }

    // שלב שליחת הודעה
    if (state.target_user?.startsWith('send:')) {
        const [, uid, phone] = state.target_user.split(':');
        const m = await client.sendMessage(uid, text);
        if (m?.id?._serialized) botMsgIds.add(m.id._serialized);
        stmts.logMsg.run(uid, 'out', text, now);
        stmts.setAdminState.run(ADMIN_NUMBER, 0, null, now);
        await safeAdminSend(`✅ נשלח ל-${phone}.`);
        return true;
    }

    // פקודות תפריט ראשי
    if (text === '1') { setBotActive(false); stmts.setAdminState.run(ADMIN_NUMBER,0,null,now); await safeAdminSend('🔴 *הבוט עצור לכולם*\nכל הודעה תועבר אליך.'); return true; }
    if (text === '2') { setBotActive(true);  stmts.setAdminState.run(ADMIN_NUMBER,0,null,now); await safeAdminSend('🟢 *הבוט פעיל לכולם*'); return true; }

    if (text === '3') {
        const convs = stmts.getAllConvs.all();
        const isHours = isBusinessHours();
        let msg = `📊 *סטטוס*\n${getBotActive() ? '🟢 פעיל' : '🔴 עצור'} | ${isHours ? '☀️ שעות פעילות' : '🌙 מחוץ לשעות'}\n\n`;
        if (!convs.length) { msg += '_אין שיחות_'; }
        else convs.forEach((c,i) => {
            const ph = c.real_phone || c.user_id.replace(/@.+/,'');
            const st = c.admin_intervened ? '👤 אתה מנהל' : c.bot_paused ? '🔴 עצור' : c.handed_off ? '⏳ ממתין' : '🤖 פעיל';
            msg += `${i+1}. ${ph} - ${st} (${timeAgo(c.last_message_at)})\n`;
        });
        stmts.setAdminState.run(ADMIN_NUMBER,0,null,now);
        await safeAdminSend(msg);
        return true;
    }

    if (text === '4') { stmts.setAdminState.run(ADMIN_NUMBER,1,'action:pause',now); await safeAdminSend(PHONE_REQ); return true; }
    if (text === '5') { stmts.setAdminState.run(ADMIN_NUMBER,1,'action:resume',now); await safeAdminSend(PHONE_REQ); return true; }
    if (text === '6') { stmts.setAdminState.run(ADMIN_NUMBER,1,'action:reset',now); await safeAdminSend(PHONE_REQ); return true; }
    if (text === '7') { stmts.setAdminState.run(ADMIN_NUMBER,1,'action:msg',now); await safeAdminSend(PHONE_REQ); return true; }

    await safeAdminSend('❓ שלח מספר 1-7 או 0.');
    return true;
}

// ========== התראה לאדמין ==========
async function notifyAdmin(uid, phone, header) {
    try {
        const hist = stmts.getMsgs.all(uid);
        const sum = hist.slice(-6).map(m => `${m.direction==='in'?'לקוח':'בוט'}: ${m.message}`).join('\n');
        const m = await client.sendMessage(ADMIN_NUMBER,
            `${header}\n\n📞 ${phone}\n🔗 https://wa.me/${phone}\n\n💬 *שיחה:*\n${sum}\n\n_שלח "בוט" לתפריט_`
        );
        if (m?.id?._serialized) botMsgIds.add(m.id._serialized);
    } catch(e) { console.error('שגיאה בהתראה:', e.message); }
}

// ========== WhatsApp ==========
const client = new Client({
    authStrategy: new LocalAuth({
        clientId: 'filterphone',
        dataPath: '/home/ubuntu/filterphone-bot/.wwebjs_auth'
    }),
    puppeteer: {
        executablePath: '/usr/bin/chromium-browser',
        headless: true,
        args: [
            '--no-sandbox','--disable-setuid-sandbox','--disable-dev-shm-usage',
            '--disable-gpu','--disable-extensions','--no-first-run','--no-zygote',
            '--disable-accelerated-2d-canvas','--memory-pressure-off'
        ],
        timeout: 180000,
        protocolTimeout: 180000
    },
    qrMaxRetries: 5,
    authTimeoutMs: 180000,
    takeoverOnConflict: true,
    takeoverTimeoutMs: 60000,
    restartOnAuthFail: true
});

client.on('qr', qr => { console.log('📱 סרוק QR:'); qrcode.generate(qr, { small: true }); });
client.on('ready', () => console.log('✅ הבוט מחובר ופעיל!'));
client.on('authenticated', () => console.log('✅ אומת'));
client.on('auth_failure', m => console.error('❌ שגיאת אימות:', m));
client.on('disconnected', r => console.log('🔌 נותק:', r));

client.on('message_create', async (message) => {
    try {
        if (message.from.includes('@g.us')) return;
        if (message.from === 'status@broadcast') return;
        if (!message.body?.trim()) return;

        // מנע לולאה - התעלם מהודעות שהבוט עצמו שלח
        const msgId = message.id?._serialized;
        if (msgId && botMsgIds.has(msgId)) {
            botMsgIds.delete(msgId);
            return;
        }

        const isFromAdmin = message.fromMe;

        if (isFromAdmin) {
            adminLastSeen = Date.now();

            // הודעה מהאדמין לעצמו - תפריט ניהול
            if (isAdminId(message.to) || isAdminId(message.from)) {
                const state = stmts.getAdminState.get(ADMIN_NUMBER);
                await handleAdmin(message, state);
                return;
            }

            // האדמין ענה ידנית ללקוח - בטל תגובת בוט וסמן כהתערבות
            const targetId = message.to;
            const cancelled = cancelPending(targetId);
            const conv = stmts.getConv.get(targetId);
            if (conv) {
                stmts.pauseConv.run(1, 1, targetId);
            } else {
                stmts.upsertConv.run(targetId, null, 0, 1, 1, Date.now());
            }
            stmts.logMsg.run(targetId, 'out', message.body.trim(), Date.now());
            console.log(`👤 התערבת ידנית בשיחה עם ${targetId}${cancelled ? ' (ביטלת תגובת בוט)' : ''}`);
            return;
        }

        // ====== הודעה מלקוח ======
        const uid = message.from;
        const text = message.body.trim();
        const phone = await getRealPhone(message);
        console.log(`📩 ${phone} (${uid}): ${text}`);

        // הבוט עצור לכולם
        if (!getBotActive()) {
            stmts.logMsg.run(uid, 'in', text, Date.now());
            stmts.upsertConv.run(uid, phone, 0, 0, 0, Date.now());
            const m = await client.sendMessage(ADMIN_NUMBER,
                `💬 *הודעה נכנסת (בוט עצור)*\n📞 ${phone}\n💭 ${text}\n🔗 https://wa.me/${phone}`
            );
            if (m?.id?._serialized) botMsgIds.add(m.id._serialized);
            return;
        }

        const conv = stmts.getConv.get(uid);

        // אתה מנהל את השיחה ידנית
        if (conv?.admin_intervened) {
            stmts.logMsg.run(uid, 'in', text, Date.now());
            stmts.upsertConv.run(uid, phone, conv.handed_off, 1, 1, Date.now());
            return;
        }

        // הבוט עצור לשיחה הזו
        if (conv?.bot_paused) {
            stmts.logMsg.run(uid, 'in', text, Date.now());
            stmts.upsertConv.run(uid, phone, conv.handed_off, 1, 0, Date.now());
            const m = await client.sendMessage(ADMIN_NUMBER,
                `💬 *הודעה (בוט עצור ללקוח)*\n📞 ${phone}\n💭 ${text}\n🔗 https://wa.me/${phone}`
            );
            if (m?.id?._serialized) botMsgIds.add(m.id._serialized);
            return;
        }

        // לקוח שהועבר לנציג
        if (conv?.handed_off) {
            stmts.logMsg.run(uid, 'in', text, Date.now());
            stmts.upsertConv.run(uid, phone, 1, 0, 0, Date.now());
            const m = await client.sendMessage(ADMIN_NUMBER,
                `💬 *הודעה מלקוח ממתין*\n📞 ${phone}\n💭 ${text}\n🔗 https://wa.me/${phone}`
            );
            if (m?.id?._serialized) botMsgIds.add(m.id._serialized);
            return;
        }

        stmts.logMsg.run(uid, 'in', text, Date.now());
        stmts.upsertConv.run(uid, phone, 0, 0, 0, Date.now());

        // ====== קבע זמן השהיה ======
        const inHours = isBusinessHours();
        let delay = DELAY_UNAVAILABLE;

        if (!inHours) {
            // מחוץ לשעות - תמיד מיידי
            delay = 0;
            console.log('🌙 מחוץ לשעות - מיידי');
        } else if (Date.now() - adminLastSeen < ADMIN_ACTIVE_MS) {
            // פעיל לאחרונה - 5 שניות
            delay = DELAY_AVAILABLE;
            console.log('⏰ אדמין פעיל - השהיה 5 שניות');
            const nm = await client.sendMessage(ADMIN_NUMBER,
                `💬 *הודעה חדשה*\n📞 ${phone}\n💭 ${text}\n_5 שניות לפני שהבוט עונה_`
            );
            if (nm?.id?._serialized) botMsgIds.add(nm.id._serialized);
        } else {
            // בדוק אם קרא ב-3 שניות
            const nm = await client.sendMessage(ADMIN_NUMBER,
                `💬 *הודעה חדשה*\n📞 ${phone}\n💭 ${text}\n🔗 https://wa.me/${phone}`
            );
            if (nm?.id?._serialized) botMsgIds.add(nm.id._serialized);
            const read = await checkAdminRead(nm, READ_CHECK_WAIT);
            if (read) {
                delay = DELAY_AVAILABLE;
                console.log('👁️ אדמין קרא - השהיה 5 שניות');
            } else {
                delay = 0;
                console.log('💤 לא קרא - מיידי');
            }
        }

        scheduleReply(uid, delay, async () => {
            // בדוק שוב שלא התערבת בינתיים
            const check = stmts.getConv.get(uid);
            if (check?.admin_intervened) {
                console.log('👤 האדמין התערב - לא עונה');
                return;
            }

            const ai = await getAIResponse(uid, text);
            console.log(`🤖 AI: ${ai}`);

            if (ai.includes('ADDRESS')) {
                const body = ai.replace(/ADDRESS/g, '').trim();
                const full = (body ? body + '\n\n' : '') + addressMsg();
                const m = await message.reply(full);
                if (m?.id?._serialized) botMsgIds.add(m.id._serialized);
                stmts.logMsg.run(uid, 'out', full, Date.now());
                stmts.upsertConv.run(uid, phone, 0, 0, 0, Date.now());

            } else if (ai.includes('HANDOFF')) {
                const body = ai.replace(/HANDOFF/g, '').trim();
                const isH = isBusinessHours();
                const msg = body || (isH
                    ? 'תודה! העברתי את פנייתך לנציג שלנו 🙏\nנחזור אליך בהקדם.'
                    : `תודה! קיבלנו את פנייתך 🙏\nנציג יחזור אליך בשעות: ${BUSINESS.hours}\n\nמוזמן להתרשם באתר: filterphone.com`);
                const m = await message.reply(msg);
                if (m?.id?._serialized) botMsgIds.add(m.id._serialized);
                stmts.logMsg.run(uid, 'out', msg, Date.now());
                stmts.upsertConv.run(uid, phone, 1, 0, 0, Date.now());
                await notifyAdmin(uid, phone, '🔔 *לקוח חדש ממתין לנציג*');

            } else {
                const m = await message.reply(ai);
                if (m?.id?._serialized) botMsgIds.add(m.id._serialized);
                stmts.logMsg.run(uid, 'out', ai, Date.now());
                stmts.upsertConv.run(uid, phone, 0, 0, 0, Date.now());
            }
        });

    } catch(e) {
        console.error('❌ שגיאה:', e.message);
        try {
            const em = await message.reply('סליחה, הייתה תקלה. נציג יחזור אליך בקרוב.');
            if (em?.id?._serialized) botMsgIds.add(em.id._serialized);
        } catch {}
    }
});

console.log('🚀 מאתחל את הבוט...');
client.initialize();
