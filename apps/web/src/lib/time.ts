export type Zone = "oslo" | "local";

const OSLO = "Europe/Oslo";
const formatters = new Map<string, Intl.DateTimeFormat>();

function formatter(zone: Zone, options: Intl.DateTimeFormatOptions): Intl.DateTimeFormat {
  const key = `${zone}|${JSON.stringify(options)}`;
  let found = formatters.get(key);
  if (!found) {
    found = new Intl.DateTimeFormat("pt-BR", {
      ...options,
      timeZone: zone === "oslo" ? OSLO : undefined,
    });
    formatters.set(key, found);
  }
  return found;
}

const toDate = (value: string | number | Date) => (value instanceof Date ? value : new Date(value));

export function formatTime(value: string | number | Date, zone: Zone, seconds = false): string {
  return formatter(zone, {
    hour: "2-digit",
    minute: "2-digit",
    second: seconds ? "2-digit" : undefined,
    hourCycle: "h23",
  }).format(toDate(value));
}

export function formatDate(value: string | number | Date, zone: Zone): string {
  return formatter(zone, { day: "2-digit", month: "2-digit", year: "numeric" }).format(toDate(value));
}

/** "UTC+2" at that instant, so the label follows daylight saving time. */
export function utcOffset(value: string | number | Date, zone: Zone): string {
  const part = formatter(zone, { timeZoneName: "shortOffset" })
    .formatToParts(toDate(value))
    .find((p) => p.type === "timeZoneName");
  const offset = (part?.value ?? "GMT").replace("GMT", "");
  return `UTC${offset || "+0"}`;
}

/** "horário de Oslo, UTC+2" or "horário local, UTC-3". */
export function zoneLabel(value: string | number | Date, zone: Zone): string {
  return `${zone === "oslo" ? "horário de Oslo" : "horário local"}, ${utcOffset(value, zone)}`;
}

/** "agora", "há 40 s", "há 12 min", "há 2 h 5 min". */
export function formatAge(since: string | number | Date, now: number): string {
  const seconds = Math.max(0, Math.round((now - toDate(since).getTime()) / 1000));
  if (seconds < 5) return "agora";
  if (seconds < 60) return `há ${seconds} s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `há ${minutes} min`;
  const hours = Math.floor(minutes / 60);
  return minutes % 60 ? `há ${hours} h ${minutes % 60} min` : `há ${hours} h`;
}

/** Duration in words for reasons: "12 min", "1 h 5 min". */
export function formatDuration(ms: number): string {
  const minutes = Math.max(0, Math.round(ms / 60000));
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  return minutes % 60 ? `${hours} h ${minutes % 60} min` : `${hours} h`;
}
