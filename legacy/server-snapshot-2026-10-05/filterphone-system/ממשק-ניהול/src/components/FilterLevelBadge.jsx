// תגית רמת סינון
const LEVELS = {
  1: { label: 'חסימה מלאה', color: '#f87171', bg: '#3a1a1a' },
  2: { label: 'טשטוש חלקי',  color: '#fb923c', bg: '#3a2a1a' },
  3: { label: 'צניעות בלבד', color: '#a78bfa', bg: '#2a1a3a' },
};

export default function FilterLevelBadge({ level }) {
  const l = LEVELS[level] || { label: `רמה ${level}`, color: '#aaa', bg: '#2a2a2a' };
  return (
    <span style={{
      display: 'inline-block',
      padding: '2px 10px',
      borderRadius: 12,
      fontSize: 12,
      fontWeight: 600,
      background: l.bg,
      color: l.color,
      border: `1px solid ${l.color}33`,
    }}>
      {level} - {l.label}
    </span>
  );
}
