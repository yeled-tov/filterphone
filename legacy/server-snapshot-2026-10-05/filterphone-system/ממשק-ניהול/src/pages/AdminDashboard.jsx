// ===================================
// Dashboard מנהל - עם טאבים
// ===================================
import { useState } from 'react';
import StatsTab from './admin/StatsTab';
import UsersTab from './admin/UsersTab';
import RequestsTab from './admin/RequestsTab';
import LogsTab from './admin/LogsTab';
import VpnTab from './admin/VpnTab';
import BlocksTab from './admin/BlocksTab';
import ImageFilterTab from './admin/ImageFilterTab';

const TABS = [
  { id: 'stats',        label: '📊 סקירה',       component: StatsTab },
  { id: 'users',        label: '👥 לקוחות',      component: UsersTab },
  { id: 'requests',     label: '📋 בקשות',       component: RequestsTab },
  { id: 'logs',         label: '📝 לוגים',       component: LogsTab },
  { id: 'vpn',          label: '🔒 VPN',          component: VpnTab },
  { id: 'blocks',       label: '🚫 חסימות',      component: BlocksTab },
  { id: 'image-filter', label: '🖼️ סינון תמונות', component: ImageFilterTab },
];

export default function AdminDashboard({ user, onLogout }) {
  const [activeTab, setActiveTab] = useState('stats');

  const ActiveComponent = TABS.find(t => t.id === activeTab)?.component || StatsTab;

  return (
    <div style={{ minHeight: '100vh', background: '#0f0f1a' }}>
      {/* Header */}
      <header style={{
        background: '#1a1a2e',
        borderBottom: '1px solid #2a2a4a',
        padding: '0 24px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        height: 60,
        position: 'sticky',
        top: 0,
        zIndex: 100,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ fontSize: 22 }}>🛡️</span>
          <span style={{ color: '#e0e0e0', fontWeight: 700, fontSize: 16 }}>FilterPhone</span>
          <span style={{ color: '#4f46e5', fontSize: 12, background: '#1a1a3a', padding: '2px 8px', borderRadius: 6 }}>מנהל</span>
        </div>

        {/* טאבים */}
        <nav style={{ display: 'flex', gap: 2 }}>
          {TABS.map(tab => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              style={{
                padding: '8px 16px',
                background: activeTab === tab.id ? '#4f46e5' : 'transparent',
                border: 'none',
                borderRadius: 6,
                color: activeTab === tab.id ? '#fff' : '#888',
                cursor: 'pointer',
                fontSize: 13,
                fontWeight: activeTab === tab.id ? 600 : 400,
                transition: 'all 0.2s',
              }}
            >
              {tab.label}
            </button>
          ))}
        </nav>

        {/* משתמש וניתוק */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <span style={{ color: '#888', fontSize: 13 }}>שלום, {user.name}</span>
          <button
            onClick={onLogout}
            style={{
              padding: '6px 14px',
              background: 'transparent',
              border: '1px solid #3a3a5a',
              borderRadius: 6,
              color: '#aaa',
              cursor: 'pointer',
              fontSize: 12,
            }}
          >
            יציאה
          </button>
        </div>
      </header>

      {/* תוכן */}
      <main style={{ padding: '28px 24px', maxWidth: 1200, margin: '0 auto' }}>
        <div className="fade-in">
          <ActiveComponent />
        </div>
      </main>
    </div>
  );
}
