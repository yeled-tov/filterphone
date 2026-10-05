// ===================================
// מסך התחברות - FilterPhone
// ===================================
import { useState } from 'react';
import { authApi } from '../services/api';
import Spinner from '../components/Spinner';

export default function LoginPage({ onLogin }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');

    if (!email || !password) {
      setError('נא למלא אימייל וסיסמה');
      return;
    }

    setLoading(true);
    try {
      const data = await authApi.login(email, password);
      localStorage.setItem('token', data.token);
      localStorage.setItem('user', JSON.stringify(data.user));
      onLogin(data.user);
    } catch (err) {
      setError(err.message || 'שגיאה בהתחברות');
    } finally {
      setLoading(false);
    }
  };

  const inputStyle = {
    width: '100%',
    padding: '12px 16px',
    background: '#0f0f1a',
    border: '1px solid #2a2a4a',
    borderRadius: 8,
    color: '#e0e0e0',
    fontSize: 15,
    outline: 'none',
    transition: 'border-color 0.2s',
    direction: 'ltr',
    textAlign: 'right',
  };

  return (
    <div style={{
      minHeight: '100vh',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      background: 'linear-gradient(135deg, #0f0f1a 0%, #1a1a2e 50%, #16213e 100%)',
      padding: 16,
    }}>
      <div style={{
        width: '100%',
        maxWidth: 400,
        background: '#1a1a2e',
        borderRadius: 16,
        padding: '40px 32px',
        border: '1px solid #2a2a4a',
        boxShadow: '0 20px 60px rgba(0,0,0,0.5)',
      }}>
        {/* לוגו */}
        <div style={{ textAlign: 'center', marginBottom: 32 }}>
          <div style={{
            width: 64, height: 64,
            background: 'linear-gradient(135deg, #7c6ff7, #4f46e5)',
            borderRadius: 16,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            margin: '0 auto 16px',
            fontSize: 28,
          }}>🛡️</div>
          <h1 style={{ fontSize: 24, color: '#e0e0e0', marginBottom: 4 }}>FilterPhone</h1>
          <p style={{ color: '#888', fontSize: 14 }}>מערכת סינון תוכן</p>
        </div>

        {/* טופס התחברות */}
        <form onSubmit={handleSubmit}>
          <div style={{ marginBottom: 16 }}>
            <label style={{ display: 'block', color: '#aaa', fontSize: 13, marginBottom: 6 }}>
              כתובת אימייל
            </label>
            <input
              type="email"
              value={email}
              onChange={e => setEmail(e.target.value)}
              placeholder="your@email.com"
              style={inputStyle}
              autoComplete="email"
              disabled={loading}
            />
          </div>

          <div style={{ marginBottom: 24 }}>
            <label style={{ display: 'block', color: '#aaa', fontSize: 13, marginBottom: 6 }}>
              סיסמה
            </label>
            <input
              type="password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              placeholder="••••••••"
              style={{ ...inputStyle, direction: 'ltr', textAlign: 'left' }}
              autoComplete="current-password"
              disabled={loading}
            />
          </div>

          {/* הודעת שגיאה */}
          {error && (
            <div style={{
              background: '#3a1a1a',
              border: '1px solid #f87171',
              borderRadius: 8,
              padding: '10px 14px',
              color: '#f87171',
              fontSize: 14,
              marginBottom: 16,
              textAlign: 'center',
            }}>
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            style={{
              width: '100%',
              padding: '13px',
              background: loading ? '#3a3a6a' : 'linear-gradient(135deg, #7c6ff7, #4f46e5)',
              border: 'none',
              borderRadius: 8,
              color: '#fff',
              fontSize: 15,
              fontWeight: 600,
              cursor: loading ? 'not-allowed' : 'pointer',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: 8,
              transition: 'opacity 0.2s',
            }}
          >
            {loading ? <><Spinner size={18} /> מתחבר...</> : 'כניסה למערכת'}
          </button>
        </form>

        {/* רמז לכניסה */}
        <div style={{
          marginTop: 24,
          padding: '12px 16px',
          background: '#0f0f1a',
          borderRadius: 8,
          fontSize: 12,
          color: '#666',
          textAlign: 'center',
          lineHeight: 1.6,
        }}>
          <strong style={{ color: '#888' }}>כניסה לדוגמה:</strong><br />
          מנהל: admin@filterphone.com / REDACTED<br />
          לקוח: israel@example.com / REDACTED
        </div>
      </div>
    </div>
  );
}
