// ===================================
// פורטל לקוח - סטטוס, סטטיסטיקות, בקשות
// ===================================
import { useState, useEffect } from 'react';
import { usersApi, requestsApi, vpnApi } from '../services/api';
import StatusBadge from '../components/StatusBadge';
import FilterLevelBadge from '../components/FilterLevelBadge';
import Modal from '../components/Modal';
import Spinner from '../components/Spinner';

const LEVEL_NAMES = { 1: 'חסימה מלאה', 2: 'טשטוש חלקי', 3: 'צניעות בלבד' };
const TYPE_NAMES = { level_change: 'שינוי רמת סינון', pause: 'השהיית סינון', resume: 'חידוש סינון' };

export default function ClientPortal({ user: initialUser, onLogout }) {
  const [user, setUser] = useState(initialUser);
  const [requests, setRequests] = useState([]);
  const [loadingRequests, setLoadingRequests] = useState(true);
  const [showModal, setShowModal] = useState(false);
  const [reqType, setReqType] = useState('level_change');
  const [reqLevel, setReqLevel] = useState(1);
  const [reqReason, setReqReason] = useState('');
  const [sending, setSending] = useState(false);
  const [reqError, setReqError] = useState('');
  const [reqSuccess, setReqSuccess] = useState('');

  const loadRequests = async () => {
    try {
      setLoadingRequests(true);
      const data = await requestsApi.getAll();
      setRequests(data);
    } catch (e) {
      // שגיאה שקטה
    } finally {
      setLoadingRequests(false);
    }
  };

  const refreshUser = async () => {
    try {
      const data = await usersApi.getMe();
      setUser(data);
    } catch (e) {}
  };

  useEffect(() => {
    loadRequests();
  }, []);

  const handleSendRequest = async (e) => {
    e.preventDefault();
    setReqError('');
    setSending(true);
    try {
      await requestsApi.create({
        type: reqType,
        requested_level: reqType === 'level_change' ? reqLevel : undefined,
        reason: reqReason || undefined,
      });
      setShowModal(false);
      setReqReason('');
      setReqSuccess('הבקשה נשלחה בהצלחה! המנהל יטפל בה בקרוב.');
      setTimeout(() => setReqSuccess(''), 5000);
      await loadRequests();
    } catch (e) {
      setReqError(e.message);
    } finally {
      setSending(false);
    }
  };

  const [vpnLoading, setVpnLoading] = useState(false);
  const [vpnQr, setVpnQr] = useState(null);
  const [vpnError, setVpnError] = useState('');

  const handleDownloadVpn = async () => {
    setVpnLoading(true);
    setVpnError('');
    try {
      const conf = await vpnApi.getMyConfig();
      const blob = new Blob([conf], { type: 'text/plain' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'filterphone-vpn.conf';
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setVpnError(e.message);
    } finally {
      setVpnLoading(false);
    }
  };

  const handleShowQr = async () => {
    setVpnLoading(true);
    setVpnError('');
    try {
      const data = await vpnApi.getMyQr();
      setVpnQr(data.qr);
    } catch (e) {
      setVpnError(e.message);
    } finally {
      setVpnLoading(false);
    }
  };

  const pendingCount = requests.filter(r => r.status === 'pending').length;
  const subscriptionDate = user.subscription_expires_at ? user.subscription_expires_at.split('T')[0] : null;
  const isExpiringSoon = subscriptionDate && new Date(subscriptionDate) < new Date(Date.now() + 30 * 24 * 60 * 60 * 1000);

  const infoRow = (label, value) => (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 0', borderBottom: '1px solid #1a1a2e' }}>
      <span style={{ color: '#888', fontSize: 14 }}>{label}</span>
      <span style={{ color: '#e0e0e0', fontSize: 14 }}>{value}</span>
    </div>
  );

  return (
    <div style={{ minHeight: '100vh', background: '#0f0f1a' }}>
      {/* Header */}
      <header style={{
        background: '#1a1a2e',
        borderBottom: '1px solid #2a2a4a',
        padding: '0 20px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        height: 60,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ fontSize: 22 }}>🛡️</span>
          <span style={{ color: '#e0e0e0', fontWeight: 700 }}>FilterPhone</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <span style={{ color: '#888', fontSize: 13 }}>שלום, {user.name}</span>
          <button
            onClick={onLogout}
            style={{ padding: '6px 14px', background: 'transparent', border: '1px solid #3a3a5a', borderRadius: 6, color: '#aaa', cursor: 'pointer', fontSize: 12 }}
          >
            יציאה
          </button>
        </div>
      </header>

      <main style={{ padding: '24px 20px', maxWidth: 700, margin: '0 auto' }}>
        {/* התראת הצלחה */}
        {reqSuccess && (
          <div style={{ background: '#1a3a2a', border: '1px solid #4ade80', borderRadius: 8, padding: '12px 16px', color: '#4ade80', marginBottom: 16, fontSize: 14 }}>
            ✓ {reqSuccess}
          </div>
        )}

        {/* כרטיס סטטוס */}
        <div style={{ background: '#1a1a2e', border: '1px solid #2a2a4a', borderRadius: 12, padding: 24, marginBottom: 20 }} className="fade-in">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 20 }}>
            <div>
              <h1 style={{ color: '#e0e0e0', fontSize: 20, marginBottom: 4 }}>פרופיל שלי</h1>
              <p style={{ color: '#888', fontSize: 13 }}>{user.email}</p>
            </div>
            <button
              onClick={refreshUser}
              style={{ background: 'none', border: 'none', color: '#7c6ff7', cursor: 'pointer', fontSize: 12 }}
            >
              ↻ רענן
            </button>
          </div>

          {infoRow('סטטוס', <StatusBadge status={user.status} />)}
          {infoRow('רמת סינון', <FilterLevelBadge level={user.filter_level} />)}
          {infoRow('פלטפורמה', user.platform || 'לא מוגדר')}
          {infoRow('מכשיר', user.device || 'לא מוגדר')}
          {infoRow('תפוגת מנוי',
            <span style={{ color: isExpiringSoon ? '#facc15' : '#e0e0e0' }}>
              {subscriptionDate || 'לא מוגדר'}
              {isExpiringSoon && ' ⚠ פג בקרוב'}
            </span>
          )}
        </div>

        {/* סטטיסטיקות */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, marginBottom: 20 }}>
          <div style={{ background: '#1a1a2e', border: '1px solid #2a2a4a', borderRadius: 12, padding: 20, textAlign: 'center' }}>
            <div style={{ fontSize: 28, fontWeight: 700, color: '#f87171' }}>
              {(user.images_blocked || 0).toLocaleString()}
            </div>
            <div style={{ color: '#888', fontSize: 13, marginTop: 4 }}>תמונות חסומות</div>
          </div>
          <div style={{ background: '#1a1a2e', border: '1px solid #2a2a4a', borderRadius: 12, padding: 20, textAlign: 'center' }}>
            <div style={{ fontSize: 28, fontWeight: 700, color: '#4ade80' }}>
              {(user.images_processed || 0).toLocaleString()}
            </div>
            <div style={{ color: '#888', fontSize: 13, marginTop: 4 }}>תמונות שעובדו</div>
          </div>
        </div>

        {/* ה-VPN שלי */}
        <div style={{ background: '#1a1a2e', border: '1px solid #2a2a4a', borderRadius: 12, padding: 24, marginBottom: 20 }}>
          <h2 style={{ color: '#e0e0e0', fontSize: 16, marginBottom: 4 }}>ה-VPN שלי</h2>
          <p style={{ color: '#888', fontSize: 13, marginBottom: 16 }}>
            חבר את המכשיר ל-VPN כדי שכל הגלישה שלך תעבור דרך הסינון המלא
          </p>

          {vpnError && (
            <div style={{ background: '#3a1a1a', border: '1px solid #f87171', borderRadius: 6, padding: '8px 12px', color: '#f87171', fontSize: 13, marginBottom: 12 }}>
              {user.vpn_ip ? `שגיאה: ${vpnError}` : 'ה-VPN שלך טרם הוגדר. פנה למנהל כדי לקבל גישה.'}
            </div>
          )}

          {user.vpn_ip ? (
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
                <span style={{ background: '#1a3a2a', color: '#4ade80', padding: '4px 12px', borderRadius: 10, fontSize: 13, fontWeight: 600 }}>
                  ✓ VPN מוגדר
                </span>
                <span style={{ color: '#555', fontSize: 12 }}>כתובת: {user.vpn_ip}</span>
              </div>

              <div style={{ display: 'flex', gap: 10, marginBottom: 16 }}>
                <button
                  onClick={handleDownloadVpn}
                  disabled={vpnLoading}
                  style={{
                    flex: 1, padding: '12px', border: 'none', borderRadius: 8,
                    background: vpnLoading ? '#2a2a4a' : 'linear-gradient(135deg, #7c6ff7, #4f46e5)',
                    color: '#fff', cursor: vpnLoading ? 'not-allowed' : 'pointer',
                    fontSize: 14, fontWeight: 600,
                  }}
                >
                  {vpnLoading ? 'מוריד...' : '⬇ הורד קובץ VPN'}
                </button>
                <button
                  onClick={handleShowQr}
                  disabled={vpnLoading}
                  style={{
                    flex: 1, padding: '12px', border: '1px solid #2a2a4a', borderRadius: 8,
                    background: 'transparent', color: '#a78bfa',
                    cursor: vpnLoading ? 'not-allowed' : 'pointer',
                    fontSize: 14, fontWeight: 600,
                  }}
                >
                  📷 הצג QR Code
                </button>
              </div>

              {vpnQr && (
                <div style={{ textAlign: 'center', marginBottom: 16 }}>
                  <img src={vpnQr} alt="WireGuard QR" style={{ width: 220, height: 220, borderRadius: 8, border: '2px solid #2a2a4a' }} />
                  <p style={{ color: '#555', fontSize: 12, marginTop: 8 }}>סרוק עם אפליקציית WireGuard</p>
                </div>
              )}

              {/* הוראות התקנה */}
              <div style={{ background: '#0f0f1a', borderRadius: 8, padding: 14 }}>
                <p style={{ color: '#aaa', fontSize: 13, fontWeight: 600, marginBottom: 10 }}>הוראות התקנה:</p>
                {[
                  {
                    platform: '🤖 Android',
                    steps: ['הורד WireGuard מ-Google Play', 'פתח את האפליקציה → לחץ +', 'בחר "Import from file or archive"', 'בחר את הקובץ שהורדת', 'הפעל את ה-VPN'],
                    link: 'https://play.google.com/store/apps/details?id=com.wireguard.android',
                    linkText: 'Google Play Store',
                  },
                  {
                    platform: '🍎 iOS',
                    steps: ['הורד WireGuard מ-App Store', 'פתח האפליקציה → לחץ +', 'בחר "Create from QR code" או "Import from Files"', 'סרוק את ה-QR או בחר את הקובץ', 'הפעל את ה-VPN'],
                    link: 'https://apps.apple.com/app/wireguard/id1441195209',
                    linkText: 'App Store',
                  },
                  {
                    platform: '💻 Windows',
                    steps: ['הורד WireGuard מהאתר הרשמי', 'התקן והפעל', 'לחץ "Import tunnel(s) from file"', 'בחר את הקובץ שהורדת', 'לחץ "Activate"'],
                    link: 'https://www.wireguard.com/install/',
                    linkText: 'wireguard.com/install',
                  },
                ].map(({ platform, steps, link, linkText }) => (
                  <div key={platform} style={{ marginBottom: 14 }}>
                    <p style={{ color: '#e0e0e0', fontSize: 13, fontWeight: 600, marginBottom: 6 }}>{platform}</p>
                    <ol style={{ color: '#888', fontSize: 12, paddingRight: 16, margin: 0 }}>
                      {steps.map((s, i) => <li key={i} style={{ marginBottom: 3 }}>{s}</li>)}
                    </ol>
                    <a
                      href={link}
                      target="_blank"
                      rel="noopener noreferrer"
                      style={{ color: '#7c6ff7', fontSize: 12, display: 'block', marginTop: 4 }}
                    >
                      הורד מ-{linkText} ↗
                    </a>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <div style={{ textAlign: 'center', padding: '20px 0' }}>
              <div style={{ fontSize: 36, marginBottom: 10 }}>🔒</div>
              <p style={{ color: '#888', fontSize: 14, marginBottom: 6 }}>ה-VPN שלך טרם הוגדר</p>
              <p style={{ color: '#555', fontSize: 13 }}>פנה למנהל המערכת לקבלת גישה ל-VPN</p>
            </div>
          )}
        </div>

        {/* שליחת בקשה */}
        <div style={{ background: '#1a1a2e', border: '1px solid #2a2a4a', borderRadius: 12, padding: 24, marginBottom: 20 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
            <h2 style={{ color: '#e0e0e0', fontSize: 16 }}>
              הבקשות שלי
              {pendingCount > 0 && (
                <span style={{ marginRight: 8, background: '#dc2626', color: '#fff', borderRadius: 10, padding: '2px 7px', fontSize: 12 }}>
                  {pendingCount}
                </span>
              )}
            </h2>
            <button
              onClick={() => { setShowModal(true); setReqError(''); }}
              style={{
                padding: '8px 16px',
                background: 'linear-gradient(135deg, #7c6ff7, #4f46e5)',
                border: 'none', borderRadius: 8, color: '#fff',
                fontSize: 13, fontWeight: 600, cursor: 'pointer',
              }}
            >
              + שלח בקשה
            </button>
          </div>

          {loadingRequests && <div style={{ textAlign: 'center', padding: 20 }}><Spinner size={24} /></div>}

          {!loadingRequests && requests.length === 0 && (
            <p style={{ color: '#555', fontSize: 14, textAlign: 'center', padding: '12px 0' }}>
              לא שלחת בקשות עדיין
            </p>
          )}

          {!loadingRequests && requests.map(req => (
            <div key={req.id} style={{
              padding: '12px 0',
              borderBottom: '1px solid #1a1a2e',
              display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 8,
            }}>
              <div>
                <div style={{ color: '#e0e0e0', fontSize: 14, marginBottom: 2 }}>
                  {TYPE_NAMES[req.type]}
                  {req.requested_level && ` → רמה ${req.requested_level} (${LEVEL_NAMES[req.requested_level]})`}
                </div>
                {req.reason && <div style={{ color: '#888', fontSize: 12 }}>"{req.reason}"</div>}
                <div style={{ color: '#555', fontSize: 11, marginTop: 2 }}>{new Date(req.created_at).toLocaleString('he-IL')}</div>
              </div>
              <StatusBadge status={req.status} />
            </div>
          ))}
        </div>

        {/* הסבר רמות סינון */}
        <div style={{ background: '#1a1a2e', border: '1px solid #2a2a4a', borderRadius: 12, padding: 24 }}>
          <h2 style={{ color: '#e0e0e0', fontSize: 16, marginBottom: 16 }}>רמות הסינון</h2>
          {[
            { level: 1, desc: 'כל התמונות הבעייתיות נחסמות לחלוטין', color: '#f87171' },
            { level: 2, desc: 'תמונות בעייתיות מטושטשות, ניתן לדלג', color: '#fb923c' },
            { level: 3, desc: 'הצגת תמונות עם המלצות צניעות בלבד', color: '#a78bfa' },
          ].map(({ level, desc, color }) => (
            <div key={level} style={{
              display: 'flex', gap: 12, alignItems: 'flex-start',
              padding: '10px 0', borderBottom: level < 3 ? '1px solid #1a1a2e' : 'none',
            }}>
              <div style={{
                width: 32, height: 32, background: color + '22', borderRadius: 8,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                color: color, fontWeight: 700, fontSize: 14, flexShrink: 0,
              }}>{level}</div>
              <div>
                <div style={{ color: color, fontWeight: 600, fontSize: 14 }}>{LEVEL_NAMES[level]}</div>
                <div style={{ color: '#888', fontSize: 13, marginTop: 2 }}>{desc}</div>
              </div>
              {user.filter_level === level && (
                <span style={{ marginRight: 'auto', color: '#4ade80', fontSize: 12, background: '#1a3a2a', padding: '2px 8px', borderRadius: 10, whiteSpace: 'nowrap' }}>
                  ✓ נוכחי
                </span>
              )}
            </div>
          ))}
        </div>
      </main>

      {/* מודל שליחת בקשה */}
      {showModal && (
        <Modal title="שליחת בקשה למנהל" onClose={() => setShowModal(false)}>
          <form onSubmit={handleSendRequest}>
            <div style={{ display: 'grid', gap: 14 }}>
              <div>
                <label style={{ display: 'block', color: '#aaa', fontSize: 13, marginBottom: 6 }}>סוג בקשה</label>
                <select
                  value={reqType}
                  onChange={e => setReqType(e.target.value)}
                  style={{ width: '100%', padding: '9px 12px', background: '#0f0f1a', border: '1px solid #2a2a4a', borderRadius: 6, color: '#e0e0e0', fontSize: 14 }}
                >
                  <option value="level_change">שינוי רמת סינון</option>
                  <option value="pause">השהיית סינון זמנית</option>
                  <option value="resume">חידוש סינון</option>
                </select>
              </div>

              {reqType === 'level_change' && (
                <div>
                  <label style={{ display: 'block', color: '#aaa', fontSize: 13, marginBottom: 6 }}>רמה מבוקשת</label>
                  <select
                    value={reqLevel}
                    onChange={e => setReqLevel(parseInt(e.target.value))}
                    style={{ width: '100%', padding: '9px 12px', background: '#0f0f1a', border: '1px solid #2a2a4a', borderRadius: 6, color: '#e0e0e0', fontSize: 14 }}
                  >
                    <option value={1}>1 - חסימה מלאה</option>
                    <option value={2}>2 - טשטוש חלקי</option>
                    <option value={3}>3 - צניעות בלבד</option>
                  </select>
                </div>
              )}

              <div>
                <label style={{ display: 'block', color: '#aaa', fontSize: 13, marginBottom: 6 }}>סיבה (אופציונלי)</label>
                <textarea
                  value={reqReason}
                  onChange={e => setReqReason(e.target.value)}
                  placeholder="הסבר למה את/ה מבקש/ת זאת..."
                  rows={3}
                  style={{ width: '100%', padding: '9px 12px', background: '#0f0f1a', border: '1px solid #2a2a4a', borderRadius: 6, color: '#e0e0e0', fontSize: 14, resize: 'vertical', fontFamily: 'inherit' }}
                />
              </div>

              {reqError && (
                <div style={{ background: '#3a1a1a', border: '1px solid #f87171', borderRadius: 6, padding: '8px 12px', color: '#f87171', fontSize: 13 }}>
                  {reqError}
                </div>
              )}

              <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
                <button
                  type="button"
                  onClick={() => setShowModal(false)}
                  style={{ padding: '9px 20px', borderRadius: 6, border: 'none', background: '#374151', color: '#fff', cursor: 'pointer', fontSize: 14 }}
                >
                  ביטול
                </button>
                <button
                  type="submit"
                  disabled={sending}
                  style={{ padding: '9px 20px', borderRadius: 6, border: 'none', background: sending ? '#3a3a6a' : 'linear-gradient(135deg, #7c6ff7, #4f46e5)', color: '#fff', cursor: sending ? 'not-allowed' : 'pointer', fontSize: 14, fontWeight: 600 }}
                >
                  {sending ? 'שולח...' : 'שלח בקשה'}
                </button>
              </div>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}
