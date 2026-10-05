// ===================================
// טאב ניהול לקוחות (מנהל)
// ===================================
import { useState, useEffect } from 'react';
import { usersApi } from '../../services/api';
import StatusBadge from '../../components/StatusBadge';
import FilterLevelBadge from '../../components/FilterLevelBadge';
import Modal from '../../components/Modal';
import Spinner from '../../components/Spinner';

const btn = (color) => ({
  padding: '5px 12px', borderRadius: 6, border: 'none',
  background: color, color: '#fff', fontSize: 12,
  cursor: 'pointer', fontWeight: 600,
});

const PLATFORMS = ['Android', 'iOS', 'Windows'];

export default function UsersTab() {
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [showModal, setShowModal] = useState(false);
  const [editUser, setEditUser] = useState(null);
  const [form, setForm] = useState({});
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState('');

  const load = async () => {
    try {
      setLoading(true);
      const data = await usersApi.getAll();
      setUsers(data.filter(u => u.role === 'client'));
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const filtered = users.filter(u =>
    u.name.includes(search) || u.email.includes(search) || (u.phone || '').includes(search)
  );

  const openCreate = () => {
    setEditUser(null);
    setForm({ name: '', email: '', phone: '', password: '', platform: 'Android', filter_level: 1, status: 'active', subscription_expires_at: '' });
    setFormError('');
    setShowModal(true);
  };

  const openEdit = (user) => {
    setEditUser(user);
    setForm({
      name: user.name, email: user.email, phone: user.phone || '',
      password: '', platform: user.platform || '', filter_level: user.filter_level,
      status: user.status, subscription_expires_at: user.subscription_expires_at || '',
    });
    setFormError('');
    setShowModal(true);
  };

  const handleSave = async (e) => {
    e.preventDefault();
    setFormError('');
    setSaving(true);
    try {
      if (editUser) {
        await usersApi.update(editUser.id, { ...form, password: form.password || undefined });
      } else {
        await usersApi.create(form);
      }
      setShowModal(false);
      await load();
    } catch (e) {
      setFormError(e.message);
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (user) => {
    if (!confirm(`למחוק את ${user.name}?`)) return;
    try {
      await usersApi.delete(user.id);
      await load();
    } catch (e) {
      alert(e.message);
    }
  };

  const handleFilterLevel = async (user) => {
    const level = parseInt(prompt(`רמת סינון חדשה ל-${user.name} (1/2/3):`, user.filter_level));
    if (![1,2,3].includes(level)) return;
    try {
      await usersApi.setFilterLevel(user.id, level);
      await load();
    } catch (e) { alert(e.message); }
  };

  const handleStatus = async (user) => {
    const opts = { active: 'פעיל', inactive: 'לא פעיל', suspended: 'מושעה' };
    const choice = prompt(`סטטוס חדש:\n1 = פעיל\n2 = לא פעיל\n3 = מושעה`);
    const map = { '1': 'active', '2': 'inactive', '3': 'suspended' };
    const status = map[choice];
    if (!status) return;
    try {
      await usersApi.setStatus(user.id, status);
      await load();
    } catch (e) { alert(e.message); }
  };

  const inputStyle = {
    width: '100%', padding: '9px 12px',
    background: '#0f0f1a', border: '1px solid #2a2a4a',
    borderRadius: 6, color: '#e0e0e0', fontSize: 14, outline: 'none',
  };

  const labelStyle = { display: 'block', color: '#aaa', fontSize: 13, marginBottom: 4 };

  return (
    <div>
      {/* כותרת ופעולות */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20, flexWrap: 'wrap', gap: 12 }}>
        <h2 style={{ color: '#e0e0e0', fontSize: 20 }}>ניהול לקוחות ({users.length})</h2>
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
          <input
            value={search} onChange={e => setSearch(e.target.value)}
            placeholder="חיפוש לפי שם / אימייל / טלפון"
            style={{ ...inputStyle, width: 240 }}
          />
          <button onClick={openCreate} style={{ ...btn('#4f46e5'), padding: '9px 18px', fontSize: 14 }}>
            + לקוח חדש
          </button>
        </div>
      </div>

      {loading && <div style={{ textAlign: 'center', padding: 40 }}><Spinner /></div>}
      {error && <div style={{ color: '#f87171', padding: 16 }}>{error}</div>}

      {/* טבלת לקוחות */}
      {!loading && (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14 }}>
            <thead>
              <tr style={{ borderBottom: '1px solid #2a2a4a' }}>
                {['שם', 'אימייל', 'טלפון', 'פלטפורמה', 'רמת סינון', 'סטטוס', 'תפוגת מנוי', 'פעולות'].map(h => (
                  <th key={h} style={{ padding: '10px 12px', color: '#888', fontWeight: 600, textAlign: 'right', whiteSpace: 'nowrap' }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.map(user => (
                <tr key={user.id} style={{ borderBottom: '1px solid #1a1a2e' }}>
                  <td style={{ padding: '10px 12px', color: '#e0e0e0', fontWeight: 500 }}>{user.name}</td>
                  <td style={{ padding: '10px 12px', color: '#aaa' }}>{user.email}</td>
                  <td style={{ padding: '10px 12px', color: '#aaa', direction: 'ltr' }}>{user.phone || '-'}</td>
                  <td style={{ padding: '10px 12px', color: '#aaa' }}>{user.platform || '-'}</td>
                  <td style={{ padding: '10px 12px' }}><FilterLevelBadge level={user.filter_level} /></td>
                  <td style={{ padding: '10px 12px' }}><StatusBadge status={user.status} /></td>
                  <td style={{ padding: '10px 12px', color: '#aaa', direction: 'ltr', whiteSpace: 'nowrap' }}>
                    {user.subscription_expires_at ? user.subscription_expires_at.split('T')[0] : '-'}
                  </td>
                  <td style={{ padding: '10px 12px' }}>
                    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                      <button onClick={() => openEdit(user)} style={btn('#4f46e5')}>עריכה</button>
                      <button onClick={() => handleFilterLevel(user)} style={btn('#7c6ff7')}>רמה</button>
                      <button onClick={() => handleStatus(user)} style={btn('#374151')}>סטטוס</button>
                      <button onClick={() => handleDelete(user)} style={btn('#dc2626')}>מחיקה</button>
                    </div>
                  </td>
                </tr>
              ))}
              {filtered.length === 0 && (
                <tr><td colSpan={8} style={{ padding: 32, textAlign: 'center', color: '#555' }}>לא נמצאו לקוחות</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {/* מודל יצירה/עריכה */}
      {showModal && (
        <Modal title={editUser ? `עריכת ${editUser.name}` : 'הוספת לקוח חדש'} onClose={() => setShowModal(false)}>
          <form onSubmit={handleSave}>
            <div style={{ display: 'grid', gap: 14 }}>
              <div>
                <label style={labelStyle}>שם מלא *</label>
                <input style={inputStyle} value={form.name} onChange={e => setForm(f => ({...f, name: e.target.value}))} required />
              </div>
              <div>
                <label style={labelStyle}>אימייל *</label>
                <input style={{...inputStyle, direction: 'ltr'}} type="email" value={form.email} onChange={e => setForm(f => ({...f, email: e.target.value}))} required />
              </div>
              <div>
                <label style={labelStyle}>טלפון</label>
                <input style={{...inputStyle, direction: 'ltr'}} value={form.phone} onChange={e => setForm(f => ({...f, phone: e.target.value}))} placeholder="050-0000000" />
              </div>
              <div>
                <label style={labelStyle}>{editUser ? 'סיסמה חדשה (ריק = ללא שינוי)' : 'סיסמה *'}</label>
                <input style={{...inputStyle, direction: 'ltr'}} type="password" value={form.password} onChange={e => setForm(f => ({...f, password: e.target.value}))} required={!editUser} />
              </div>
              <div>
                <label style={labelStyle}>פלטפורמה</label>
                <select style={inputStyle} value={form.platform} onChange={e => setForm(f => ({...f, platform: e.target.value}))}>
                  <option value="">בחר...</option>
                  {PLATFORMS.map(p => <option key={p} value={p}>{p}</option>)}
                </select>
              </div>
              <div>
                <label style={labelStyle}>רמת סינון</label>
                <select style={inputStyle} value={form.filter_level} onChange={e => setForm(f => ({...f, filter_level: parseInt(e.target.value)}))}>
                  <option value={1}>1 - חסימה מלאה</option>
                  <option value={2}>2 - טשטוש חלקי</option>
                  <option value={3}>3 - צניעות בלבד</option>
                </select>
              </div>
              <div>
                <label style={labelStyle}>סטטוס</label>
                <select style={inputStyle} value={form.status} onChange={e => setForm(f => ({...f, status: e.target.value}))}>
                  <option value="active">פעיל</option>
                  <option value="inactive">לא פעיל</option>
                  <option value="suspended">מושעה</option>
                </select>
              </div>
              <div>
                <label style={labelStyle}>תפוגת מנוי</label>
                <input style={{...inputStyle, direction: 'ltr'}} type="date" value={form.subscription_expires_at} onChange={e => setForm(f => ({...f, subscription_expires_at: e.target.value}))} />
              </div>

              {formError && (
                <div style={{ background: '#3a1a1a', border: '1px solid #f87171', borderRadius: 6, padding: '8px 12px', color: '#f87171', fontSize: 13 }}>
                  {formError}
                </div>
              )}

              <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', marginTop: 4 }}>
                <button type="button" onClick={() => setShowModal(false)} style={{ ...btn('#374151'), padding: '9px 20px', fontSize: 14 }}>ביטול</button>
                <button type="submit" disabled={saving} style={{ ...btn('#4f46e5'), padding: '9px 20px', fontSize: 14 }}>
                  {saving ? 'שומר...' : (editUser ? 'עדכן' : 'צור לקוח')}
                </button>
              </div>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}
