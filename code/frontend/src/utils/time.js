const SEOUL_TZ = "Asia/Seoul";

function toDate(value) {
  if (!value) return null;
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatDateTimeSeoul(value) {
  const date = toDate(value);
  if (!date) return value || "-";
  return date.toLocaleString("ko-KR", { timeZone: SEOUL_TZ, hour12: false });
}

export function formatDateSeoul(value) {
  const date = toDate(value);
  if (!date) return value || "-";
  return date.toLocaleDateString("ko-KR", { timeZone: SEOUL_TZ });
}
