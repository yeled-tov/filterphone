// תגית סטטוס צבעונית
const STATUS_COLORS = {
  active:    { bg: '#1a3a2a', color: '#4ade80', label: 'פעיל' },
  inactive:  { bg: '#2a2a1a', color: '#facc15', label: 'לא פעיל' },
  suspended: { bg: '#3a1a1a', color: '#f87171', label: 'מושעה' },
  pending:   { bg: '#1a2a3a', color: '#60a5fa', label: 'ממתין' },
  approved:  { bg: '#1a3a2a', color: '#4ade80', label: 'אושר' },
  rejected:  { bg: '#3a1a1a', color: '#f87171', label: 'נדחה' },
};

export default function StatusBadge({ status }) {
  const s = STATUS_COLORS[status] || { bg: '#2a2a2a', color: '#aaa', label: status };
  return (
    <span style={{
      display: 'inline-block',
      padding: '2px 10px',
      borderRadius: 12,
      fontSize: 12,
      fontWeight: 600,
      background: s.bg,
      color: s.color,
      border: `1px solid ${s.color}33`,
    }}>
      {s.label}
    </span>
  );
}
