// ===================================
// טאב חסימות DNS - AdGuard Home
// ===================================
import { useState, useEffect } from 'react';
import { adguardApi } from '../../services/api';
import Spinner from '../../components/Spinner';

export default function BlocksTab() {
  const [domains, setDomains] = useState([]);
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const [newDomain, setNewDomain] = useState('');
  const [adding, setAdding] = useState(false);
  const [deletingDomain, setDeletingDomain] = useState(null);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');

  const loadData = async () => {
    try {
      setLoading(true);
      const [blockedRes, statsRes] = await Promise.all([
        adguardApi.getBlockedList(),
        adguardApi.getStats(),
      ]);
      setDomains(blockedRes.domains || []);
      setStats(statsRes);
    } catch (e) {
      setError('שגיאה בטעינה: ' + e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadData(); }, []);

  const showMsg = (msg, isError = false) => {
    if (isError) { setError(msg); setSuccess(''); }
    else { setSuccess(msg); setError(''); }
    setTimeout(() => { setError(''); setSuccess(''); }, 4000);
  };

  const handleBlock = async (e) => {
    e.preventDefault();
    const domain = newDomain.trim();
    if (!domain) return;

    setAdding(true);
    try {
      await adguardApi.blockDomain(domain);
      showMsg(`הדומיין ${domain} נחסם בהצלחה`);
      setNewDomain('');
      await loadData();
    } catch (e) {
      showMsg('שגיאה בחסימה: ' + e.message, true);
    } finally {
      setAdding(false);
    }
  };

  const handleUnblock = async (domain) => {
    if (!confirm(`לבטל חסימה של ${domain}?`)) return;
    setDeletingDomain(domain);
    try {
      await adguardApi.unblockDomain(domain);
      showMsg(`חסימה של ${domain} בוטלה`);
      await loadData();
    } catch (e) {
      showMsg('שגיאה בביטול חסימה: ' + e.message, true);
    } finally {
      setDeletingDomain(null);
    }
  };

  const card = { background: '#1a1a2e', border: '1px solid #2a2a4a', borderRadius: 12, padding: 24 };

  const statCard = (value, label, color) => (
    <div style={{ ...card, textAlign: 'center', padding: 20 }}>
      <div style={{ fontSize: 26, fontWeight: 700, color }}>{value}</div>
      <div style={{ color: '#888', fontSize: 12, marginTop: 4 }}>{label}</div>
    </div>
  );

  return (
    <div style={{ display: 'grid', gap: 20 }}>
      {/* כותרת */}
      <div>
        <h1 style={{ color: '#e0e0e0', fontSize: 22, margin: 0 }}>חסימות DNS</h1>
        <p style={{ color: '#888', fontSize: 13, marginTop: 4 }}>
          ניהול חסימות ב-AdGuard Home · פועל על פורט 53
        </p>
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

      {/* סטטיסטיקות */}
      {stats && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 14 }}>
          {statCard(stats.dns_queries?.toLocaleString() || '0', 'שאילתות DNS היום', '#60a5fa')}
          {statCard(stats.blocked_today?.toLocaleString() || '0', 'חסומות היום', '#f87171')}
          {statCard(`${stats.blocked_percent || 0}%`, 'אחוז חסימה', '#fb923c')}
        </div>
      )}

      {stats && !stats.running && (
        <div style={{ background: '#2a1a00', border: '1px solid #fb923c', borderRadius: 8, padding: '12px 16px', color: '#fb923c', fontSize: 14 }}>
          ⚠ AdGuard Home אינו פעיל כרגע. הרץ: <code>sudo systemctl start adguardhome</code>
        </div>
      )}

      {/* הוספת דומיין */}
      <div style={card}>
        <h2 style={{ color: '#e0e0e0', fontSize: 16, marginBottom: 16 }}>חסימת דומיין חדש</h2>
        <form onSubmit={handleBlock} style={{ display: 'flex', gap: 10 }}>
          <input
            type="text"
            value={newDomain}
            onChange={e => setNewDomain(e.target.value)}
            placeholder="לדוגמה: tiktok.com"
            disabled={adding}
            style={{
              flex: 1, padding: '10px 14px',
              background: '#0f0f1a', border: '1px solid #2a2a4a',
              borderRadius: 8, color: '#e0e0e0', fontSize: 14,
              fontFamily: 'monospace',
            }}
          />
          <button
            type="submit"
            disabled={adding || !newDomain.trim()}
            style={{
              padding: '10px 20px', border: 'none', borderRadius: 8,
              background: adding || !newDomain.trim()
                ? '#2a2a4a'
                : 'linear-gradient(135deg, #dc2626, #b91c1c)',
              color: '#fff', cursor: adding ? 'not-allowed' : 'pointer',
              fontWeight: 600, fontSize: 14, whiteSpace: 'nowrap',
            }}
          >
            {adding ? <Spinner size={16} /> : '🚫 חסום'}
          </button>
        </form>
        <p style={{ color: '#555', fontSize: 12, marginTop: 8 }}>
          לדוגמה: tiktok.com, instagram.com, pornhub.com
        </p>
      </div>

      {/* רשימת חסומים */}
      <div style={card}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
          <h2 style={{ color: '#e0e0e0', fontSize: 16, margin: 0 }}>
            דומיינים חסומים ידנית
            <span style={{ marginRight: 8, background: '#3a1a1a', color: '#f87171', padding: '2px 8px', borderRadius: 10, fontSize: 12 }}>
              {domains.length}
            </span>
          </h2>
          <button
            onClick={loadData}
            style={{ background: 'none', border: 'none', color: '#7c6ff7', cursor: 'pointer', fontSize: 12 }}
          >
            ↻ רענן
          </button>
        </div>

        {loading && <div style={{ textAlign: 'center', padding: 30 }}><Spinner size={24} /></div>}

        {!loading && domains.length === 0 && (
          <p style={{ color: '#555', textAlign: 'center', padding: '16px 0', fontSize: 14 }}>
            אין דומיינים חסומים ידנית
          </p>
        )}

        {!loading && domains.map(domain => (
          <div key={domain} style={{
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            padding: '10px 0', borderBottom: '1px solid #1a1a2e',
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <span style={{ color: '#f87171', fontSize: 13 }}>🚫</span>
              <code style={{ color: '#e0e0e0', fontSize: 14 }}>{domain}</code>
            </div>
            <button
              onClick={() => handleUnblock(domain)}
              disabled={deletingDomain === domain}
              style={{
                padding: '4px 12px', border: '1px solid #3a3a5a',
                borderRadius: 6, background: 'transparent',
                color: deletingDomain === domain ? '#555' : '#aaa',
                cursor: deletingDomain === domain ? 'not-allowed' : 'pointer',
                fontSize: 12,
              }}
            >
              {deletingDomain === domain ? '...' : 'בטל חסימה'}
            </button>
          </div>
        ))}

        {/* הערה על רשימות מובנות */}
        <div style={{ marginTop: 16, padding: '12px 14px', background: '#0f0f1a', borderRadius: 8 }}>
          <p style={{ color: '#555', fontSize: 12, margin: 0 }}>
            💡 AdGuard Home כולל רשימות חסימה מובנות (פרסומות, פורנו, מעקב).
            רשימה זו מציגה רק חסימות ידניות שנוספו כאן.
          </p>
        </div>
      </div>

      {/* Top חסומים */}
      {stats?.top_blocked && stats.top_blocked.length > 0 && (
        <div style={card}>
          <h2 style={{ color: '#e0e0e0', fontSize: 16, marginBottom: 16 }}>Top 5 דומיינים חסומים היום</h2>
          {stats.top_blocked.slice(0, 5).map(([domain, count]) => (
            <div key={domain} style={{
              display: 'flex', justifyContent: 'space-between',
              padding: '8px 0', borderBottom: '1px solid #1a1a2e',
            }}>
              <code style={{ color: '#e0e0e0', fontSize: 13 }}>{domain}</code>
              <span style={{ color: '#f87171', fontSize: 13, fontWeight: 600 }}>{count.toLocaleString()}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
