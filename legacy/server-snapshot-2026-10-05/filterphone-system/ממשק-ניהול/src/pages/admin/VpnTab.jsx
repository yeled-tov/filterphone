// ===================================
// טאב VPN - ניהול קבצי WireGuard ללקוחות
// ===================================
import { useState, useEffect } from 'react';
import { usersApi, vpnApi } from '../../services/api';
import Spinner from '../../components/Spinner';
import Modal from '../../components/Modal';

export default function VpnTab() {
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [actionId, setActionId] = useState(null); // userId שמבצעים עליו פעולה
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [qrModal, setQrModal] = useState(null); // { name, qr }

  const loadUsers = async () => {
    try {
      setLoading(true);
      const data = await usersApi.getAll();
      // רק לקוחות (לא מנהלים)
      setUsers(data.filter(u => u.role === 'client'));
    } catch (e) {
      setError('שגיאה בטעינת משתמשים: ' + e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadUsers(); }, []);

  const showMsg = (msg, isError = false) => {
    if (isError) { setError(msg); setSuccess(''); }
    else { setSuccess(msg); setError(''); }
    setTimeout(() => { setError(''); setSuccess(''); }, 5000);
  };

  // יצירת VPN ללקוח
  const handleGenerate = async (user) => {
    setActionId(user.id);
    try {
      const res = await vpnApi.generate(user.id);
      showMsg(`VPN נוצר עבור ${user.name} (IP: ${res.vpnIp})`);
      await loadUsers();
    } catch (e) {
      showMsg('שגיאה ביצירת VPN: ' + e.message, true);
    } finally {
      setActionId(null);
    }
  };

  // הצגת QR
  const handleShowQr = async (user) => {
    setActionId(user.id);
    try {
      const res = await vpnApi.getQrForUser(user.id);
      setQrModal({ name: user.name, qr: res.qr });
    } catch (e) {
      showMsg('שגיאה בטעינת QR: ' + e.message, true);
    } finally {
      setActionId(null);
    }
  };

  // הורדת קובץ קונפיג
  const handleDownload = async (user) => {
    setActionId(user.id);
    try {
      const conf = await vpnApi.getConfigForUser(user.id);
      const blob = new Blob([conf], { type: 'text/plain' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `filterphone-${user.name.replace(/\s/g, '-')}.conf`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      showMsg('שגיאה בהורדת קונפיג: ' + e.message, true);
    } finally {
      setActionId(null);
    }
  };

  // מחיקת VPN
  const handleDelete = async (user) => {
    if (!confirm(`למחוק את ה-VPN של ${user.name}?`)) return;
    setActionId(user.id);
    try {
      await vpnApi.delete(user.id);
      showMsg(`VPN של ${user.name} נמחק`);
      await loadUsers();
    } catch (e) {
      showMsg('שגיאה במחיקת VPN: ' + e.message, true);
    } finally {
      setActionId(null);
    }
  };

  const card = { background: '#1a1a2e', border: '1px solid #2a2a4a', borderRadius: 12, padding: 24 };
  const btn = (extra = {}) => ({
    padding: '6px 14px', border: 'none', borderRadius: 6,
    cursor: 'pointer', fontSize: 12, fontWeight: 600, ...extra,
  });

  return (
    <div style={{ display: 'grid', gap: 20 }}>
      {/* כותרת */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <h1 style={{ color: '#e0e0e0', fontSize: 22, margin: 0 }}>ניהול VPN</h1>
          <p style={{ color: '#888', fontSize: 13, marginTop: 4 }}>
            יצירת קבצי WireGuard VPN ללקוחות · DNS מסונן דרך AdGuard Home
          </p>
        </div>
      </div>

      {/* הודעות */}
      {error && (
        <div style={{ background: '#3a1a1a', border: '1px solid #f87171', borderRadius: 8, padding: '12px 16px', color: '#f87171', fontSize: 14 }}>
          ✗ {error}
        </div>
      )}
      {success && (
        <div style={{ background: '#1a3a2a', border: '1px solid #4ade80', borderRadius: 8, padding: '12px 16px', color: '#4ade80', fontSize: 14 }}>
          ✓ {success}
        </div>
      )}

      {/* טבלת לקוחות */}
      <div style={card}>
        <h2 style={{ color: '#e0e0e0', fontSize: 16, marginBottom: 16 }}>
          לקוחות · {users.length}
        </h2>

        {loading && <div style={{ textAlign: 'center', padding: 40 }}><Spinner size={28} /></div>}

        {!loading && users.length === 0 && (
          <p style={{ color: '#555', textAlign: 'center', padding: 20 }}>אין לקוחות</p>
        )}

        {!loading && users.map(user => (
          <div key={user.id} style={{
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            padding: '14px 0', borderBottom: '1px solid #1a1a2e', gap: 12,
          }}>
            {/* פרטי לקוח */}
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ color: '#e0e0e0', fontSize: 14, fontWeight: 600 }}>{user.name}</div>
              <div style={{ color: '#666', fontSize: 12, marginTop: 2 }}>{user.email}</div>
            </div>

            {/* סטטוס VPN */}
            <div style={{ textAlign: 'center', minWidth: 120 }}>
              {user.vpn_ip ? (
                <div>
                  <span style={{ background: '#1a3a2a', color: '#4ade80', padding: '3px 10px', borderRadius: 10, fontSize: 12, fontWeight: 600 }}>
                    VPN פעיל
                  </span>
                  <div style={{ color: '#555', fontSize: 11, marginTop: 4 }}>{user.vpn_ip}</div>
                </div>
              ) : (
                <span style={{ background: '#2a2a2a', color: '#666', padding: '3px 10px', borderRadius: 10, fontSize: 12 }}>
                  ללא VPN
                </span>
              )}
            </div>

            {/* כפתורי פעולה */}
            <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
              {actionId === user.id ? (
                <Spinner size={20} />
              ) : user.vpn_ip ? (
                <>
                  <button onClick={() => handleShowQr(user)} style={btn({ background: '#1a3a5a', color: '#60a5fa' })}>
                    QR
                  </button>
                  <button onClick={() => handleDownload(user)} style={btn({ background: '#1a2a3a', color: '#a78bfa' })}>
                    הורד
                  </button>
                  <button onClick={() => handleDelete(user)} style={btn({ background: '#3a1a1a', color: '#f87171' })}>
                    מחק VPN
                  </button>
                </>
              ) : (
                <button
                  onClick={() => handleGenerate(user)}
                  style={btn({ background: 'linear-gradient(135deg, #7c6ff7, #4f46e5)', color: '#fff' })}
                >
                  צור VPN
                </button>
              )}
            </div>
          </div>
        ))}
      </div>

      {/* מודל QR */}
      {qrModal && (
        <Modal title={`QR Code - ${qrModal.name}`} onClose={() => setQrModal(null)}>
          <div style={{ textAlign: 'center' }}>
            <p style={{ color: '#888', fontSize: 13, marginBottom: 16 }}>
              סרוק עם אפליקציית WireGuard במכשיר הלקוח
            </p>
            {qrModal.qr ? (
              <img
                src={qrModal.qr}
                alt="WireGuard QR"
                style={{ width: 260, height: 260, borderRadius: 8, border: '2px solid #2a2a4a' }}
              />
            ) : (
              <p style={{ color: '#f87171' }}>שגיאה בטעינת QR</p>
            )}
            <p style={{ color: '#555', fontSize: 12, marginTop: 12 }}>
              לאחר הסריקה, הפעל את ה-VPN במכשיר
            </p>
          </div>
        </Modal>
      )}
    </div>
  );
}
