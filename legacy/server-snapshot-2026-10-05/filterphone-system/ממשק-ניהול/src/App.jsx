// ===================================
// App.jsx - ניתוב ראשי לפי role
// ===================================
import { useState, useEffect } from 'react';
import LoginPage from './pages/LoginPage';
import AdminDashboard from './pages/AdminDashboard';
import ClientPortal from './pages/ClientPortal';
import Spinner from './components/Spinner';

export default function App() {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  // בדיקת JWT קיים בטעינה
  useEffect(() => {
    const token = localStorage.getItem('token');
    const savedUser = localStorage.getItem('user');

    if (token && savedUser) {
      try {
        const parsedUser = JSON.parse(savedUser);
        // בדיקה שהטוקן לא פג (בדיקת exp)
        const payload = JSON.parse(atob(token.split('.')[1]));
        if (payload.exp * 1000 > Date.now()) {
          setUser(parsedUser);
        } else {
          // טוקן פג - ניקוי
          localStorage.removeItem('token');
          localStorage.removeItem('user');
        }
      } catch {
        localStorage.removeItem('token');
        localStorage.removeItem('user');
      }
    }
    setLoading(false);
  }, []);

  const handleLogin = (userData) => {
    setUser(userData);
  };

  const handleLogout = () => {
    localStorage.removeItem('token');
    localStorage.removeItem('user');
    setUser(null);
  };

  // טעינה ראשונית
  if (loading) {
    return (
      <div style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: '#0f0f1a',
        flexDirection: 'column',
        gap: 16,
      }}>
        <Spinner size={40} />
        <p style={{ color: '#666', fontSize: 14 }}>טוען...</p>
      </div>
    );
  }

  // לא מחובר - הצג מסך כניסה
  if (!user) {
    return <LoginPage onLogin={handleLogin} />;
  }

  // מנהל - Dashboard מלא
  if (user.role === 'admin') {
    return <AdminDashboard user={user} onLogout={handleLogout} />;
  }

  // לקוח - פורטל לקוח
  return <ClientPortal user={user} onLogout={handleLogout} />;
}
