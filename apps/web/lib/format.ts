export function formatDate(value: string | Date | null | undefined): string {
  if (!value) return "—";
  const date = typeof value === "string" ? new Date(value) : value;
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** "5m ago", or "in 23h" for a moment still to come — an expiry, a deadline. */
export function formatRelative(value: string | null | undefined): string {
  if (!value) return "—";
  const diffMs = Date.now() - new Date(value).getTime();
  const future = diffMs < 0;
  const minutes = Math.round(Math.abs(diffMs) / 60000);
  if (minutes < 1) return future ? "any moment" : "just now";
  const span =
    minutes < 60
      ? `${minutes}m`
      : minutes < 60 * 24
        ? `${Math.round(minutes / 60)}h`
        : minutes < 60 * 24 * 30
          ? `${Math.round(minutes / 60 / 24)}d`
          : `${Math.round(minutes / 60 / 24 / 30)}mo`;
  return future ? `in ${span}` : `${span} ago`;
}

export function formatNumber(value: number | undefined): string {
  return new Intl.NumberFormat().format(value ?? 0);
}

export function percent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

export function titleCase(value: string): string {
  return value.replace(/[_-]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
