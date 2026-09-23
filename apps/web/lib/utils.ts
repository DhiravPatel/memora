import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

/*
 * One tinted-border swatch per category. Every badge is the same shape; only the hue
 * changes, so the eye reads type by colour and never by decoration.
 */
export const MEMORY_TYPE_COLORS: Record<string, string> = {
  problem: "border-danger/70 bg-danger/10 text-danger",
  preference: "border-violet/70 bg-violet/10 text-violet",
  goal: "border-info/70 bg-info/10 text-info",
  subscription: "border-success/70 bg-success/10 text-success",
  behavior: "border-warning/70 bg-warning/10 text-warning",
  feedback: "border-violet/70 bg-violet/10 text-violet",
  intent: "border-info/70 bg-info/10 text-info",
  fact: "border-border-strong bg-surface-2 text-foreground",
  relationship: "border-info/70 bg-info/10 text-info",
  summary: "border-accent/70 bg-accent/10 text-accent",
};

export const STATUS_COLORS: Record<string, string> = {
  processed: "border-success/70 bg-success/10 text-success",
  pending: "border-warning/70 bg-warning/10 text-warning",
  processing: "border-info/70 bg-info/10 text-info",
  skipped: "border-border bg-surface-2 text-muted-foreground",
  failed: "border-danger/70 bg-danger/10 text-danger",
  active: "border-success/70 bg-success/10 text-success",
  superseded: "border-border bg-surface-2 text-muted-foreground",
  expired: "border-warning/70 bg-warning/10 text-warning",
  deleted: "border-danger/70 bg-danger/10 text-danger",
};

export const HEALTH_BAND_COLORS: Record<string, string> = {
  healthy: "border-success/70 bg-success/10 text-success",
  watch: "border-warning/70 bg-warning/10 text-warning",
  at_risk: "border-danger/70 bg-danger/10 text-danger",
  critical: "border-critical bg-critical/10 text-critical",
};

export const LINK_TYPE_LABELS: Record<string, string> = {
  caused_by: "caused by",
  resolved_by: "resolved by",
  relates_to: "relates to",
  preceded_by: "preceded by",
};

/**
 * Literal mirrors of the tokens in app/globals.css, for the two libraries that paint into
 * SVG attributes and so cannot read a CSS variable (Recharts, React Flow). Keep in step
 * with `:root` — everything else in the app should use the Tailwind colour names.
 */
export const THEME = {
  accent: "#c81e3d",
  danger: "#961328",
  success: "#008059",
  warning: "#9e5a00",
  info: "#2e67d1",
  violet: "#992e90",
  foreground: "#1a1714",
  mutedForeground: "#67615b",
  faint: "#746e67",
  border: "#e5e1dc",
  borderStrong: "#c5beb5",
  surface: "#ffffff",
  surface2: "#f7f5f3",
  canvas: "#fbfaf9",
} as const;

/**
 * Categorical chart palette, in fixed order — assigned by series identity, never cycled.
 *
 * Four slots, not five: a fifth hue could not be separated from these under all-pairs
 * colour-vision checks, and the honest fix for a fifth series is a second chart, not a
 * colour nobody can distinguish. Validated for lightness band, chroma floor, CVD
 * separation and contrast against the chart surface. Red and green sit in the 6-8 CVD
 * band, which is why every series is also direct-labelled at the end of its line.
 */
export const CHART_COLORS = [
  THEME.accent, // #c81e3d
  THEME.info, // #2e67d1
  THEME.success, // #008059
  THEME.violet, // #992e90
];

export const ROLE_COLORS: Record<string, string> = {
  owner: "border-accent/70 bg-accent/10 text-accent",
  admin: "border-info/70 bg-info/10 text-info",
  member: "border-border-strong bg-surface-2 text-foreground",
  viewer: "border-border bg-surface-2 text-muted-foreground",
};

export const SCOPE_COLORS: Record<string, string> = {
  admin: "border-danger/70 bg-danger/10 text-danger",
  "events:write": "border-warning/70 bg-warning/10 text-warning",
  "memory:write": "border-violet/70 bg-violet/10 text-violet",
  "customers:write": "border-violet/70 bg-violet/10 text-violet",
  "memory:restricted": "border-danger/70 bg-danger/10 text-danger",
  "memory:read": "border-info/70 bg-info/10 text-info",
  "customers:read": "border-info/70 bg-info/10 text-info",
};

export const INVITATION_COLORS: Record<string, string> = {
  pending: "border-warning/70 bg-warning/10 text-warning",
  accepted: "border-success/70 bg-success/10 text-success",
  revoked: "border-border bg-surface-2 text-muted-foreground",
  expired: "border-border bg-surface-2 text-muted-foreground",
};
