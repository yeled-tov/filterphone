// ===================================
// טאב בקשות (מנהל)
// ===================================
import { useState, useEffect } from 'react';
import { requestsApi } from '../../services/api';
import StatusBadge from '../../components/StatusBadge';
import Spinner from '../../components/Spinner';

const btn = (color) => ({
  padding: '5px 14px', borderRadius: 6, border: 'none',
  background: color, color: '#fff', fontSize: 12, cursor: 'pointer', fontWeight: 600,
});

const TYPE_LABELS = {
  level_change: 'שינוי רמת סינון',
  pause: 'השהיית סינון',
  resume: 'חידוש סינון',
};

const LEVEL_LABELS = { 1: 'חסימה מלאה', 2: 'טשטוש חלקי', 3: 'צניעות בלבד' };

export default function RequestsTab() {
  const [requests, setRequests] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [filter, setFilter] = useState('pending');
  const [actionLoading, setActionLoading] = useState(null);

  const load = async () => {
    try {
      setLoading(true);
      const data = await requestsApi.getAll();
      setRequests(data);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const filtered = filter === 'all' ? requests : requests.filter(r => r.status === filter);
  const pendingCount = requests.filter(r => r.status === 'pending').length;

  const handleApprove = async (id) => {
    setActionLoading(id + '_approve');
    try {
      await requestsApi.approve(id);
      await load();
    } catch (e) { alert(e.message); }
    finally { setActionLoading(null); }
  };

  const handleReject = async (id) => {
    const reason = prompt('סיבת דחייה (אופציונלי):');
    if (reason === null) return; // ביטל
    setActionLoading(id + '_reject');
    try {
      await requestsApi.reject(id, reason);
      await load();
    } catch (e) { alert(e.message); }
    finally { setActionLoading(null); }
  };

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20, flexWrap: 'wrap', gap: 12 }}>
        <h2 style={{ color: '#e0e0e0', fontSize: 20 }}>
          בקשות לקוחות
          {pendingCount > 0 && (
            <span style={{ marginRight: 10, background: '#dc2626', color: '#fff', borderRadius: 10, padding: '2px 8px', fontSize: 13 }}>
              {pendingCount} ממתינות
            </span>
          )}
        </h2>

        {/* פילטר */}
        <div style={{ display: 'flex', gap: 6 }}>
          {[['pending', 'ממתינות'], ['approved', 'אושרו'], ['rejected', 'נדחו'], ['all', 'הכל']].map(([val, label]) => (
            <button
              key={val}
              onClick={() => setFilter(val)}
              style={{
                padding: '6px 14px', borderRadius: 6, border: 'none', fontSize: 13, cursor: 'pointer',
                background: filter === val ? '#4f46e5' : '#1a1a2e',
                color: filter === val ? '#fff' : '#aaa',
              }}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {loading && <div style={{ textAlign: 'center', padding: 40 }}><Spinner /></div>}
      {error && <div style={{ color: '#f87171', padding: 16 }}>{error}</div>}

      {!loading && (
        <div style={{ display: 'grid', gap: 12 }}>
          {filtered.map(req => (
            <div key={req.id} style={{
              background: '#0f0f1a',
              border: req.status === 'pending' ? '1px solid #4f46e5' : '1px solid #1a1a2e',
              borderRadius: 10,
              padding: '16px 20px',
              display: 'grid',
              gridTemplateColumns: '1fr auto',
              gap: 12,
              alignItems: 'start',
            }}>
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8, flexWrap: 'wrap' }}>
                  <span style={{ color: '#e0e0e0', fontWeight: 600 }}>{req.user_name}</span>
                  <span style={{ color: '#888', fontSize: 13 }}>{req.user_email}</span>
                  <StatusBadge status={req.status} />
                  <span style={{ color: '#7c6ff7', fontSize: 13, background: '#1a1a3a', padding: '2px 10px', borderRadius: 10 }}>
                    {TYPE_LABELS[req.type] || req.type}
                  </span>
                </div>
                {req.requested_level && (
                  <div style={{ color: '#aaa', fontSize: 13, marginBottom: 4 }}>
                    רמה מבוקשת: <strong style={{ color: '#fb923c' }}>
                      {req.requested_level} - {LEVEL_LABELS[req.requested_level]}
                    </strong>
                  </div>
                )}
                {req.reason && (
                  <div style={{ color: '#888', fontSize: 13, marginBottom: 4 }}>
                    סיבה: "{req.reason}"
                  </div>
                )}
                <div style={{ color: '#555', fontSize: 12 }}>
                  {new Date(req.created_at).toLocaleString('he-IL')}
                  {req.resolved_at && ` • טופל: ${new Date(req.resolved_at).toLocaleString('he-IL')}`}
                </div>
              </div>

              {req.status === 'pending' && (
                <div style={{ display: 'flex', gap: 8 }}>
                  <button
                    onClick={() => handleApprove(req.id)}
                    disabled={actionLoading === req.id + '_approve'}
                    style={btn('#16a34a')}
                  >
                    {actionLoading === req.id + '_approve' ? '...' : '✓ אשר'}
                  </button>
                  <button
                    onClick={() => handleReject(req.id)}
                    disabled={actionLoading === req.id + '_reject'}
                    style={btn('#dc2626')}
                  >
                    {actionLoading === req.id + '_reject' ? '...' : '✗ דחה'}
                  </button>
                </div>
              )}
            </div>
          ))}

          {filtered.length === 0 && (
            <div style={{ textAlign: 'center', padding: 40, color: '#555' }}>
              {filter === 'pending' ? 'אין בקשות ממתינות' : 'לא נמצאו בקשות'}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
