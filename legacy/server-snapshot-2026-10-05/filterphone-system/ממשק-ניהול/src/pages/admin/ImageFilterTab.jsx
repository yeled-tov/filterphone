// ===================================
// טאב סינון תמונות - גרסה חדשה
// mitmproxy במצב regular proxy + DeepFace + NudeNet
// ===================================
import { useState, useEffect, useCallback } from 'react';
import { imageFilterApi } from '../../services/api';
import Spinner from '../../components/Spinner';

const S = {
  card: {
    background: '#1a1a2e',
    border: '1px solid #2a2a4a',
    borderRadius: 12,
    padding: 20,
    marginBottom: 20,
  },
  cardTitle: {
    color: '#e0e0e0',
    fontWeight: 700,
    fontSize: 15,
    marginBottom: 16,
    display: 'flex',
    alignItems: 'center',
    gap: 8,
  },
  grid2: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
    gap: 12,
  },
  statBox: {
    background: '#0f0f1a',
    borderRadius: 8,
    padding: '14px 18px',
    textAlign: 'center',
  },
  statNum: { fontSize: 28, fontWeight: 800, color: '#4f46e5' },
  statLabel: { fontSize: 12, color: '#888', marginTop: 4 },
  badge: (ok) => ({
    display: 'inline-block',
    padding: '3px 10px',
    borderRadius: 20,
    fontSize: 12,
    fontWeight: 600,
    background: ok ? '#064e3b' : '#450a0a',
    color: ok ? '#34d399' : '#f87171',
  }),
  row: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: '10px 0',
    borderBottom: '1px solid #1e1e3a',
    fontSize: 13,
    color: '#ccc',
  },
  btn: (color = '#4f46e5') => ({
    padding: '7px 16px',
    background: color,
    border: 'none',
    borderRadius: 6,
    color: '#fff',
    cursor: 'pointer',
    fontSize: 13,
    fontWeight: 600,
  }),
  btnGhost: {
    padding: '7px 16px',
    background: 'transparent',
    border: '1px solid #3a3a5a',
    borderRadius: 6,
    color: '#aaa',
    cursor: 'pointer',
    fontSize: 13,
  },
  input: {
    background: '#0f0f1a',
    border: '1px solid #3a3a5a',
    borderRadius: 6,
    padding: '8px 12px',
    color: '#e0e0e0',
    fontSize: 13,
    width: '100%',
    boxSizing: 'border-box',
  },
  error: {
    background: '#450a0a',
    border: '1px solid #7f1d1d',
    borderRadius: 8,
    padding: '10px 14px',
    color: '#f87171',
    fontSize: 13,
    marginBottom: 12,
  },
  success: {
    background: '#064e3b',
    border: '1px solid #065f46',
    borderRadius: 8,
    padding: '10px 14px',
    color: '#34d399',
    fontSize: 13,
    marginBottom: 12,
  },
  infoBox: {
    marginTop: 16,
    padding: 14,
    background: '#0f0f1a',
    borderRadius: 8,
    fontSize: 12,
    color: '#888',
    lineHeight: 1.8,
  },
};

export default function ImageFilterTab() {
  const [status, setStatus] = useState(null);
  const [stats, setStats] = useState(null);
  const [domains, setDomains] = useState([]);
  const [loading, setLoading] = useState(true);
  const [newDomain, setNewDomain] = useState('');
  const [msg, setMsg] = useState({ text: '', type: '' });
  const [restarting, setRestarting] = useState(false);

  const showMsg = (text, type = 'success') => {
    setMsg({ text, type });
    setTimeout(() => setMsg({ text: '', type: '' }), 5000);
  };

  const loadAll = useCallback(async () => {
    setLoading(true);
    try {
      const [st, statsRes, domainsRes] = await Promise.allSettled([
        imageFilterApi.getStatus(),
        imageFilterApi.getStats(),
        imageFilterApi.getDomains(),
      ]);
      if (st.status === 'fulfilled') setStatus(st.value);
      if (statsRes.status === 'fulfilled') setStats(statsRes.value);
      if (domainsRes.status === 'fulfilled') setDomains(domainsRes.value.domains || []);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadAll(); }, [loadAll]);

  const handleRestart = async () => {
    if (!confirm('להפעיל מחדש את שרת סינון התמונות?')) return;
    setRestarting(true);
    try {
      await imageFilterApi.restart();
      showMsg('שרת סינון התמונות הופעל מחדש');
      setTimeout(loadAll, 3000);
    } catch (e) {
      showMsg('שגיאה: ' + e.message, 'error');
    } finally {
      setRestarting(false);
    }
  };

  const handleAddDomain = async () => {
    if (!newDomain.trim()) return;
    try {
      await imageFilterApi.addDomain(newDomain.trim());
      showMsg(`הדומיין ${newDomain} נוסף לרשימת הסינון`);
      setNewDomain('');
      const res = await imageFilterApi.getDomains();
      setDomains(res.domains || []);
    } catch (e) {
      showMsg('שגיאה: ' + e.message, 'error');
    }
  };

  const handleRemoveDomain = async (domain) => {
    if (!confirm(`להסיר את ${domain} מרשימת הסינון?`)) return;
    try {
      await imageFilterApi.removeDomain(domain);
      showMsg(`${domain} הוסר`);
      setDomains(prev => prev.filter(d => d !== domain));
    } catch (e) {
      showMsg('שגיאה: ' + e.message, 'error');
    }
  };

  if (loading) {
    return (
      <div style={{ textAlign: 'center', padding: 60 }}>
        <Spinner size={36} />
        <p style={{ color: '#666', marginTop: 12 }}>טוען נתוני סינון...</p>
      </div>
    );
  }

  const blockRate = stats?.block_rate || '0';

  return (
    <div>
      <h2 style={{ color: '#e0e0e0', margin: '0 0 24px', fontSize: 22 }}>
        סינון תמונות
      </h2>

      {msg.text && <div style={msg.type === 'error' ? S.error : S.success}>{msg.text}</div>}

      {/* סטטוס שירותים */}
      <div style={S.card}>
        <div style={S.cardTitle}>סטטוס שירותים</div>
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 16 }}>
          <ServiceBadge label="MITM Proxy (mitmproxy)" ok={status?.mitm?.running} />
          <ServiceBadge label="Redis Cache" ok={status?.redis?.running} />
        </div>

        <div style={{ display: 'flex', gap: 8 }}>
          <button onClick={handleRestart} style={S.btn('#6d28d9')} disabled={restarting}>
            {restarting ? 'מפעיל...' : 'הפעל מחדש'}
          </button>
          <button onClick={loadAll} style={S.btnGhost}>רענן</button>
        </div>
      </div>

      {/* סטטיסטיקות */}
      <div style={S.card}>
        <div style={S.cardTitle}>סטטיסטיקות סינון</div>
        <div style={S.grid2}>
          <div style={S.statBox}>
            <div style={S.statNum}>{stats?.images_scanned ?? 0}</div>
            <div style={S.statLabel}>תמונות שנסרקו</div>
          </div>
          <div style={S.statBox}>
            <div style={{ ...S.statNum, color: '#f87171' }}>{stats?.images_blocked ?? 0}</div>
            <div style={S.statLabel}>תמונות שנחסמו</div>
          </div>
          <div style={S.statBox}>
            <div style={{ ...S.statNum, color: '#34d399' }}>{stats?.images_passed ?? 0}</div>
            <div style={S.statLabel}>תמונות שעברו</div>
          </div>
          <div style={S.statBox}>
            <div style={{ ...S.statNum, color: '#818cf8' }}>{stats?.cache_hits ?? 0}</div>
            <div style={S.statLabel}>Cache Hits</div>
          </div>
          <div style={S.statBox}>
            <div style={{ ...S.statNum, color: '#fbbf24' }}>{blockRate}%</div>
            <div style={S.statLabel}>אחוז חסימה</div>
          </div>
        </div>
      </div>

      {/* ניהול דומיינים */}
      <div style={S.card}>
        <div style={S.cardTitle}>דומיינים מסוננים ({domains.length})</div>
        <div style={{ fontSize: 12, color: '#666', marginBottom: 12, lineHeight: 1.6 }}>
          רק תעבורת דפדפן לדומיינים ברשימה הזו תיבדק.
          אפליקציות עם certificate pinning עוברות חופשי דרך ה-proxy בלי בעיה.
        </div>

        {/* הוספת דומיין */}
        <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
          <input
            style={S.input}
            placeholder="*.example.com"
            value={newDomain}
            onChange={e => setNewDomain(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleAddDomain()}
          />
          <button onClick={handleAddDomain} style={{ ...S.btn(), whiteSpace: 'nowrap' }}>
            + הוסף
          </button>
        </div>

        {/* רשימה */}
        <div style={{ maxHeight: 400, overflowY: 'auto' }}>
          {domains.map((domain, i) => (
            <div key={i} style={S.row}>
              <span style={{ fontFamily: 'monospace', color: '#93c5fd' }}>{domain}</span>
              <button
                onClick={() => handleRemoveDomain(domain)}
                style={{ ...S.btn('#7f1d1d'), padding: '4px 10px', fontSize: 12 }}
              >
                הסר
              </button>
            </div>
          ))}
        </div>
      </div>

      {/* הוראות */}
      <div style={S.card}>
        <div style={S.cardTitle}>הוראות הגדרה ללקוח</div>
        <div style={S.infoBox}>
          <div style={{ fontWeight: 700, color: '#ccc', marginBottom: 8 }}>
            הגדרת Proxy בטלפון:
          </div>
          <div>1. היכנס להגדרות WiFi בטלפון</div>
          <div>2. לחץ על הרשת המחוברת &rarr; Proxy &rarr; ידני</div>
          <div>3. Host: <span style={{ color: '#93c5fd', fontFamily: 'monospace' }}>10.8.0.1</span></div>
          <div>4. Port: <span style={{ color: '#93c5fd', fontFamily: 'monospace' }}>8080</span></div>
          <div style={{ marginTop: 8, color: '#666' }}>
            * בעתיד, האפליקציה הייעודית תגדיר את זה אוטומטית
          </div>
          <div style={{ marginTop: 12, borderTop: '1px solid #2a2a4a', paddingTop: 12 }}>
            <div style={{ fontWeight: 700, color: '#ccc', marginBottom: 8 }}>
              התקנת תעודת CA (נדרש ל-HTTPS):
            </div>
            <a
              href="http://151.145.88.42:3001/api/image-filter/ca-cert"
              style={{ color: '#4f46e5', textDecoration: 'none', fontWeight: 600 }}
              target="_blank"
              rel="noreferrer"
            >
              הורד FilterPhone CA Certificate
            </a>
          </div>
        </div>
      </div>
    </div>
  );
}

function ServiceBadge({ label, ok }) {
  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      gap: 8,
      background: '#0f0f1a',
      borderRadius: 8,
      padding: '8px 14px',
    }}>
      <span style={{
        width: 8, height: 8, borderRadius: '50%',
        background: ok ? '#34d399' : '#f87171',
        display: 'inline-block',
        flexShrink: 0,
      }} />
      <span style={{ color: '#ccc', fontSize: 13 }}>{label}</span>
      <span style={S.badge(ok)}>{ok ? 'פעיל' : 'כבוי'}</span>
    </div>
  );
}
