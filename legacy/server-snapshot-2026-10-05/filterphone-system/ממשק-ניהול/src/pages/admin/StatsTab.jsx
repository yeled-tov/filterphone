// ===================================
// טאב סטטיסטיקות / Dashboard (מנהל)
// ===================================
import { useState, useEffect } from 'react';
import { logsApi } from '../../services/api';
import Spinner from '../../components/Spinner';

const Card = ({ title, value, sub, color = '#7c6ff7', icon }) => (
  <div style={{
    background: '#0f0f1a',
    border: `1px solid ${color}33`,
    borderRadius: 12,
    padding: '20px 24px',
    display: 'flex',
    alignItems: 'center',
    gap: 16,
  }}>
    <div style={{
      width: 48, height: 48,
      background: color + '22',
      borderRadius: 12,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      fontSize: 22,
    }}>{icon}</div>
    <div>
      <div style={{ color: '#888', fontSize: 12, marginBottom: 2 }}>{title}</div>
      <div style={{ color: color, fontSize: 28, fontWeight: 700 }}>{value}</div>
      {sub && <div style={{ color: '#666', fontSize: 12, marginTop: 2 }}>{sub}</div>}
    </div>
  </div>
);

const LEVEL_INFO = {
  1: { label: 'חסימה מלאה', color: '#f87171' },
  2: { label: 'טשטוש חלקי', color: '#fb923c' },
  3: { label: 'צניעות בלבד', color: '#a78bfa' },
};

export default function StatsTab() {
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = async () => {
    try {
      setLoading(true);
      const data = await logsApi.getStats();
      setStats(data);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  if (loading) return <div style={{ textAlign: 'center', padding: 60 }}><Spinner size={40} /></div>;
  if (error) return <div style={{ color: '#f87171', padding: 16 }}>{error}</div>;
  if (!stats) return null;

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 24 }}>
        <h2 style={{ color: '#e0e0e0', fontSize: 20 }}>סקירה כללית</h2>
        <button onClick={load} style={{ padding: '6px 14px', background: '#1a1a2e', border: '1px solid #2a2a4a', borderRadius: 6, color: '#aaa', cursor: 'pointer', fontSize: 13 }}>
          רענן
        </button>
      </div>

      {/* כרטיסי סטטיסטיקה */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 14, marginBottom: 28 }}>
        <Card title="סה״כ לקוחות" value={stats.users.total} icon="👥" color="#7c6ff7" />
        <Card title="לקוחות פעילים" value={stats.users.active} icon="✅" color="#4ade80" sub={`${stats.users.inactive} לא פעילים`} />
        <Card title="מושעים" value={stats.users.suspended} icon="🚫" color="#f87171" />
        <Card title="בקשות ממתינות" value={stats.requests.pending} icon="📋" color="#facc15" sub={`מתוך ${stats.requests.total} בקשות`} />
        <Card title="תמונות חסומות" value={(stats.images.blocked || 0).toLocaleString()} icon="🖼️" color="#fb923c" sub={`מתוך ${(stats.images.processed || 0).toLocaleString()} שעובדו`} />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: 20 }}>
        {/* התפלגות רמות סינון */}
        <div style={{ background: '#0f0f1a', border: '1px solid #1a1a2e', borderRadius: 12, padding: 20 }}>
          <h3 style={{ color: '#aaa', fontSize: 14, marginBottom: 16 }}>התפלגות רמות סינון</h3>
          {stats.filterLevels.map(({ filter_level, count }) => {
            const info = LEVEL_INFO[filter_level] || { label: `רמה ${filter_level}`, color: '#aaa' };
            const pct = stats.users.total ? Math.round((count / stats.users.total) * 100) : 0;
            return (
              <div key={filter_level} style={{ marginBottom: 12 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                  <span style={{ color: info.color, fontSize: 13 }}>רמה {filter_level} - {info.label}</span>
                  <span style={{ color: '#888', fontSize: 13 }}>{count} ({pct}%)</span>
                </div>
                <div style={{ height: 6, background: '#1a1a2e', borderRadius: 3, overflow: 'hidden' }}>
                  <div style={{ width: `${pct}%`, height: '100%', background: info.color, borderRadius: 3 }} />
                </div>
              </div>
            );
          })}
        </div>

        {/* התפלגות פלטפורמות */}
        <div style={{ background: '#0f0f1a', border: '1px solid #1a1a2e', borderRadius: 12, padding: 20 }}>
          <h3 style={{ color: '#aaa', fontSize: 14, marginBottom: 16 }}>פלטפורמות</h3>
          {stats.platforms.map(({ platform, count }) => {
            const icons = { Android: '🤖', iOS: '🍎', Windows: '🪟' };
            const colors = { Android: '#4ade80', iOS: '#60a5fa', Windows: '#a78bfa' };
            const pct = stats.users.total ? Math.round((count / stats.users.total) * 100) : 0;
            return (
              <div key={platform} style={{ marginBottom: 12 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                  <span style={{ color: '#e0e0e0', fontSize: 13 }}>{icons[platform] || '📱'} {platform}</span>
                  <span style={{ color: '#888', fontSize: 13 }}>{count} ({pct}%)</span>
                </div>
                <div style={{ height: 6, background: '#1a1a2e', borderRadius: 3, overflow: 'hidden' }}>
                  <div style={{ width: `${pct}%`, height: '100%', background: colors[platform] || '#7c6ff7', borderRadius: 3 }} />
                </div>
              </div>
            );
          })}
          {stats.platforms.length === 0 && <p style={{ color: '#555', fontSize: 13 }}>אין נתונים</p>}
        </div>

        {/* פעילות אחרונה */}
        <div style={{ background: '#0f0f1a', border: '1px solid #1a1a2e', borderRadius: 12, padding: 20, gridColumn: 'span 2' }}>
          <h3 style={{ color: '#aaa', fontSize: 14, marginBottom: 16 }}>פעילות אחרונה</h3>
          <div style={{ display: 'grid', gap: 8 }}>
            {stats.recentActivity.map(log => (
              <div key={log.id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '8px 0', borderBottom: '1px solid #1a1a2e', flexWrap: 'wrap', gap: 8 }}>
                <div>
                  <span style={{ color: '#e0e0e0', fontSize: 13 }}>{log.action}</span>
                  {log.detail && <span style={{ color: '#888', fontSize: 12 }}> - {log.detail}</span>}
                </div>
                <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
                  {log.user_name && <span style={{ color: '#7c6ff7', fontSize: 12 }}>{log.user_name}</span>}
                  <span style={{ color: '#555', fontSize: 11, direction: 'ltr' }}>
                    {new Date(log.created_at).toLocaleString('he-IL')}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
