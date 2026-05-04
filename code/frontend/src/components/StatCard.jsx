export default function StatCard({ label, value, subText }) {
  return (
    <div className="stat-card">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      <div className="stat-sub">{subText}</div>
    </div>
  );
}