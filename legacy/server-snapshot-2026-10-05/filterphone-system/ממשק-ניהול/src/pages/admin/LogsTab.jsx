// ===================================
// טאב לוגים (מנהל)
// ===================================
import { useState, useEffect } from 'react';
import { logsApi } from '../../services/api';
import Spinner from '../../components/Spinner';

const LEVEL_COLORS = {
  info:    { color: '#60a5fa', bg: '#1a2a3a', icon: 'ℹ' },
  warning: { color: '#facc15', bg: '#2a2a1a', icon: '⚠' },
  error:   { color: '#f87171', bg: '#3a1a1a', icon: '✕' },
};

export default function LogsTab() {
  const [logs, setLogs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [levelFilter, setLevelFilter] = useState('');
  const [limit, setLimit] = useState(100);

  const load = async () => {
    try {
      setLoading(true);
      const params = { limit };
      if (levelFilter) params.level = levelFilter;
      const data = await logsApi.getLogs(params);
      setLogs(data);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, [levelFilter, limit]);

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20, flexWrap: 'wrap', gap: 12 }}>
        <h2 style={{ color: '#e0e0e0', fontSize: 20 }}>לוג פעילות ({logs.length})</h2>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <select
            value={levelFilter}
            onChange={e => setLevelFilter(e.target.value)}
            style={{ padding: '7px 12px', background: '#0f0f1a', border: '1px solid #2a2a4a', borderRadius: 6, color: '#e0e0e0', fontSize: 13 }}
          >
            <option value="">כל הרמות</option>
            <option value="info">מידע</option>
            <option value="warning">אזהרה</option>
            <option value="error">שגיאה</option>
          </select>
          <select
            value={limit}
            onChange={e => setLimit(parseInt(e.target.value))}
            style={{ padding: '7px 12px', background: '#0f0f1a', border: '1px solid #2a2a4a', borderRadius: 6, color: '#e0e0e0', fontSize: 13 }}
          >
            <option value={50}>50 שורות</option>
            <option value={100}>100 שורות</option>
            <option value={200}>200 שורות</option>
            <option value={500}>500 שורות</option>
          </select>
          <button
            onClick={load}
            style={{ padding: '7px 14px', background: '#1a1a2e', border: '1px solid #2a2a4a', borderRadius: 6, color: '#aaa', cursor: 'pointer', fontSize: 13 }}
          >
            רענן
          </button>
        </div>
      </div>

      {loading && <div style={{ textAlign: 'center', padding: 40 }}><Spinner /></div>}
      {error && <div style={{ color: '#f87171', padding: 16 }}>{error}</div>}

      {!loading && (
        <div style={{ display: 'grid', gap: 6 }}>
          {logs.map(log => {
            const lv = LEVEL_COLORS[log.level] || LEVEL_COLORS.info;
            return (
              <div key={log.id} style={{
                display: 'grid',
                gridTemplateColumns: '28px 160px 1fr auto',
                gap: 12,
                alignItems: 'center',
                padding: '10px 14px',
                background: lv.bg,
                border: `1px solid ${lv.color}22`,
                borderRadius: 8,
                fontSize: 13,
              }}>
                <span style={{ color: lv.color, fontSize: 15, textAlign: 'center' }}>{lv.icon}</span>
                <span style={{ color: '#888', direction: 'ltr', whiteSpace: 'nowrap' }}>
                  {new Date(log.created_at).toLocaleString('he-IL')}
                </span>
                <div>
                  <span style={{ color: '#e0e0e0', fontWeight: 500 }}>{log.action}</span>
                  {log.detail && <span style={{ color: '#aaa' }}> — {log.detail}</span>}
                  {log.user_name && (
                    <span style={{ marginRight: 8, color: '#7c6ff7', fontSize: 12 }}>
                      ({log.user_name})
                    </span>
                  )}
                </div>
                <span style={{ color: lv.color, fontSize: 11, background: lv.bg, border: `1px solid ${lv.color}44`, padding: '2px 8px', borderRadius: 10, whiteSpace: 'nowrap' }}>
                  {log.level}
                </span>
              </div>
            );
          })}
          {logs.length === 0 && (
            <div style={{ textAlign: 'center', padding: 40, color: '#555' }}>לא נמצאו לוגים</div>
          )}
        </div>
      )}
    </div>
  );
}
