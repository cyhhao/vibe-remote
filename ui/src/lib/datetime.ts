// Format an ISO timestamp in the viewer's local timezone as
// "YYYY-MM-DD HH:MM:SS GMT+8" — no "T" separator, no "+00:00" offset chain,
// just a single tz label. Harness stores times in UTC/ISO; the cards localize
// them so the user reads schedules on their own clock. Returns "—" for empty
// input and the raw string for unparseable input (never throws).
export function formatLocalDateTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const p = (n: number) => String(n).padStart(2, '0');
  const date = `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
  const time = `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
  return `${date} ${time} ${localTzLabel(d)}`;
}

// "GMT+8" / "GMT+5:30" / "GMT-7" / "GMT" — one human tz label for the date's
// local offset. Derived from the Date itself, so it stays DST-correct.
export function localTzLabel(d: Date = new Date()): string {
  const offsetMin = -d.getTimezoneOffset(); // minutes east of UTC
  if (offsetMin === 0) return 'GMT';
  const sign = offsetMin > 0 ? '+' : '-';
  const abs = Math.abs(offsetMin);
  const h = Math.floor(abs / 60);
  const m = abs % 60;
  return `GMT${sign}${h}${m ? ':' + String(m).padStart(2, '0') : ''}`;
}
