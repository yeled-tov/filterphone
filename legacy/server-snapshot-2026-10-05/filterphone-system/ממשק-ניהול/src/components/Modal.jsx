// חלון מודאלי גנרי
export default function Modal({ title, onClose, children }) {
  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0,
        background: 'rgba(0,0,0,0.7)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        zIndex: 1000, padding: 16,
      }}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          background: '#1a1a2e',
          border: '1px solid #2a2a4a',
          borderRadius: 12,
          padding: 24,
          width: '100%',
          maxWidth: 500,
          maxHeight: '90vh',
          overflowY: 'auto',
          animation: 'fadeIn 0.2s ease',
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
          <h3 style={{ color: '#e0e0e0', fontSize: 18 }}>{title}</h3>
          <button
            onClick={onClose}
            style={{
              background: 'none', border: 'none', color: '#888',
              fontSize: 20, cursor: 'pointer', padding: '0 4px',
            }}
          >✕</button>
        </div>
        {children}
      </div>
    </div>
  );
}
