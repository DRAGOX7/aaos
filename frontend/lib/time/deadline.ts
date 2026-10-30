/** Deadlines are instants; both academic views use the viewer's timezone. */
export function formatDeadline(value: string, locale: string | undefined, timeZone?: string): string {
  return new Intl.DateTimeFormat(locale, {
    day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit",
    timeZoneName: "short", ...(timeZone ? { timeZone } : {}),
  }).format(new Date(value));
}
