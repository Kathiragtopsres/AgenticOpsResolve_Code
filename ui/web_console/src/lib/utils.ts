import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

// Common date/time formatter. Renders an ISO-8601 timestamp (or a plain
// YYYY-MM-DD date) as "YYYY-MM-DD HH:mm" in UTC, e.g. "2026-10-06 08:25".
// Returns "" for null/empty/unparseable input, and leaves non-timestamp
// strings (e.g. already-formatted text) unchanged.
export function formatDateTime(value: unknown): string {
  if (value === null || value === undefined) return "";
  const s = String(value).trim();
  if (s === "") return "";
  const d = new Date(s);
  if (isNaN(d.getTime())) return s; // not a parseable date; show as-is
  const pad = (n: number) => String(n).padStart(2, "0");
  const yyyy = d.getUTCFullYear();
  const mm = pad(d.getUTCMonth() + 1);
  const dd = pad(d.getUTCDate());
  const hh = pad(d.getUTCHours());
  const mi = pad(d.getUTCMinutes());
  // Calendar-date-only inputs (YYYY-MM-DD) have no time component.
  if (/^\d{4}-\d{2}-\d{2}$/.test(s)) return `${yyyy}-${mm}-${dd}`;
  return `${yyyy}-${mm}-${dd} ${hh}:${mi}`;
}

// Current date+time in UTC as "YYYY-MM-DD HH:mm". 
export function nowUtc(): string {
  return formatDateTime(new Date().toISOString());
}

// Current time-of-day in UTC as "HH:mm:ss". 
export function nowUtcTime(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}`;
}
