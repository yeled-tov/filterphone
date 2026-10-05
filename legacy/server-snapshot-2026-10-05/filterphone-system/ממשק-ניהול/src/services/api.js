// ===================================
// שירות חיבור ל-API של השרת
// ===================================

const API_URL = 'http://151.145.88.42:3001';

// פונקציה עזר לשליחת בקשות עם JWT
async function request(path, options = {}) {
  const token = localStorage.getItem('token');

  const headers = {
    'Content-Type': 'application/json',
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...options.headers,
  };

  const res = await fetch(`${API_URL}${path}`, {
    ...options,
    headers,
  });

  const data = await res.json();

  if (!res.ok) {
    throw new Error(data.error || 'שגיאה בשרת');
  }

  return data;
}

// ===== אימות =====
export const authApi = {
  login: (email, password) =>
    request('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    }),

  me: () => request('/api/auth/me'),
};

// ===== משתמשים =====
export const usersApi = {
  getAll: () => request('/api/users'),

  getMe: () => request('/api/users/me'),

  create: (userData) =>
    request('/api/users', {
      method: 'POST',
      body: JSON.stringify(userData),
    }),

  update: (id, userData) =>
    request(`/api/users/${id}`, {
      method: 'PUT',
      body: JSON.stringify(userData),
    }),

  delete: (id) =>
    request(`/api/users/${id}`, { method: 'DELETE' }),

  setFilterLevel: (id, filter_level) =>
    request(`/api/users/${id}/filter-level`, {
      method: 'PUT',
      body: JSON.stringify({ filter_level }),
    }),

  setStatus: (id, status) =>
    request(`/api/users/${id}/status`, {
      method: 'PUT',
      body: JSON.stringify({ status }),
    }),
};

// ===== בקשות =====
export const requestsApi = {
  getAll: () => request('/api/requests'),

  create: (requestData) =>
    request('/api/requests', {
      method: 'POST',
      body: JSON.stringify(requestData),
    }),

  approve: (id) =>
    request(`/api/requests/${id}/approve`, { method: 'PUT' }),

  reject: (id, reason) =>
    request(`/api/requests/${id}/reject`, {
      method: 'PUT',
      body: JSON.stringify({ reason }),
    }),
};

// ===== לוגים וסטטיסטיקות =====
export const logsApi = {
  getLogs: (params = {}) => {
    const query = new URLSearchParams(params).toString();
    return request(`/api/logs${query ? '?' + query : ''}`);
  },

  getStats: () => request('/api/stats'),
};

// ===== VPN =====
export const vpnApi = {
  // מנהל: יצירת VPN ללקוח
  generate: (userId) =>
    request(`/api/vpn/generate/${userId}`, { method: 'POST' }),

  // מנהל: QR code של לקוח ספציפי (מוריד קובץ ומייצר QR)
  getQrForUser: async (userId) => {
    // קודם מורידים את הקונפיג, ואז מבקשים QR דרך my-qr עם impersonation - אין endpoint ישיר,
    // לכן נשתמש בנתיב הייעודי עם userId בשרת
    const token = localStorage.getItem('token');
    const res = await fetch(`${API_URL}/api/vpn/${userId}/qr`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!res.ok) {
      const data = await res.json();
      throw new Error(data.error || 'שגיאה בטעינת QR');
    }
    return res.json();
  },

  // מנהל: הורדת קונפיג של לקוח ספציפי
  getConfigForUser: async (userId) => {
    const token = localStorage.getItem('token');
    const res = await fetch(`${API_URL}/api/vpn/${userId}/config`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.error || 'שגיאה בהורדת קונפיג');
    }
    return res.text();
  },

  // מנהל: מחיקת VPN של לקוח
  delete: (userId) =>
    request(`/api/vpn/${userId}`, { method: 'DELETE' }),

  // לקוח: קבלת הקונפיג שלי
  getMyConfig: async () => {
    const token = localStorage.getItem('token');
    const res = await fetch(`${API_URL}/api/vpn/my-config`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.error || 'שגיאה בהורדת קונפיג');
    }
    return res.text();
  },

  // לקוח: QR code שלי
  getMyQr: () => request('/api/vpn/my-qr'),
};

// ===== סינון תמונות =====
export const imageFilterApi = {
  getStatus:  () => request('/api/image-filter/status'),
  getStats:   () => request('/api/image-filter/stats'),
  getDomains: () => request('/api/image-filter/domains'),
  addDomain: (domain) =>
    request('/api/image-filter/domains', {
      method: 'POST',
      body: JSON.stringify({ domain }),
    }),
  removeDomain: (domain) =>
    request(`/api/image-filter/domains/${encodeURIComponent(domain)}`, {
      method: 'DELETE',
    }),
  restart: () => request('/api/image-filter/restart', { method: 'POST' }),
};

// ===== AdGuard / חסימות DNS =====
export const adguardApi = {
  getStats: () => request('/api/adguard/stats'),

  getBlockedList: () => request('/api/adguard/blocked-list'),

  blockDomain: (domain) =>
    request('/api/adguard/block-domain', {
      method: 'POST',
      body: JSON.stringify({ domain }),
    }),

  unblockDomain: (domain) =>
    request('/api/adguard/unblock-domain', {
      method: 'POST',
      body: JSON.stringify({ domain }),
    }),
};
