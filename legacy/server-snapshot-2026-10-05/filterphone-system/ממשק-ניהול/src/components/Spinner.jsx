// טעינה - ספינר
export default function Spinner({ size = 32 }) {
  return (
    <div style={{
      width: size,
      height: size,
      border: `3px solid #2a2a4a`,
      borderTop: `3px solid #7c6ff7`,
      borderRadius: '50%',
      animation: 'spin 0.8s linear infinite',
      display: 'inline-block',
    }} />
  );
}
