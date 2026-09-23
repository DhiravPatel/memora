"use client";

/**
 * The shared vocabulary for the forward-looking features: trajectory, signal strength,
 * goal progress and recommendation priority. Kept in one file so a signal looks the same
 * on the portfolio list as it does on a customer's profile.
 */

import Link from "next/link";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/states";
import { formatRelative } from "@/lib/format";
import { cn } from "@/lib/utils";
import type {
  Goal,
  GoalStatus,
  Recommendation,
  Signal,
  SignalPoint,
  SignalReport,
  Trajectory,
} from "@/lib/types";

const TRAJECTORY_STYLES: Record<Trajectory, string> = {
  improving: "border-success/70 bg-success/10 text-success",
  steady: "border-border-strong bg-surface-2 text-foreground",
  declining: "border-danger/70 bg-danger/10 text-danger",
};

const TRAJECTORY_ARROWS: Record<Trajectory, string> = {
  improving: "↗",
  steady: "→",
  declining: "↘",
};

const PRIORITY_STYLES: Record<Recommendation["priority"], string> = {
  now: "border-danger bg-danger text-accent-foreground",
  soon: "border-warning/70 bg-warning/10 text-warning",
  when_you_can: "border-border-strong bg-surface-2 text-muted-foreground",
};

const GOAL_STYLES: Record<GoalStatus, string> = {
  open: "border-info/70 bg-info/10 text-info",
  progressing: "border-accent/70 bg-accent/10 text-accent",
  achieved: "border-success/70 bg-success/10 text-success",
  stalled: "border-warning/70 bg-warning/10 text-warning",
  abandoned: "border-border bg-surface-2 text-muted-foreground",
};

export function TrajectoryBadge({
  trajectory,
  className,
}: {
  trajectory: Trajectory;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-sm border px-2 py-0.5 font-mono text-[10px] uppercase tracking-label",
        TRAJECTORY_STYLES[trajectory],
        className,
      )}
    >
      <span aria-hidden>{TRAJECTORY_ARROWS[trajectory]}</span>
      {trajectory}
    </span>
  );
}

export function GoalStatusBadge({ status }: { status: GoalStatus }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-sm rounded-sm border px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-label",
        GOAL_STYLES[status],
      )}
    >
      {status}
    </span>
  );
}

/** Risk left of centre, opportunity right of it — direction is readable at a glance. */
export function SignalBar({ signal }: { signal: Signal }) {
  const width = Math.max(4, Math.round(signal.strength * 100));
  const risk = signal.direction === "risk";
  return (
    <div className="flex h-2 w-24 shrink-0 border border-border bg-surface-3" title={`${Math.round(signal.strength * 100)}%`}>
      <div
        className={cn("h-full", risk ? "bg-danger" : "bg-success")}
        style={{ width: `${width}%` }}
      />
    </div>
  );
}

export function SignalList({ signals }: { signals: Signal[] }) {
  if (!signals.length) {
    return (
      <EmptyState
        title="No signals"
        description="Nothing in this customer's memory points either way yet."
      />
    );
  }
  return (
    <ul className="divide-y divide-border">
      {signals.map((signal) => (
        <li key={signal.key} className="flex items-start gap-4 px-5 py-3.5">
          <SignalBar signal={signal} />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <span className="label-strong">{signal.label}</span>
              <span
                className={cn(
                  "label",
                  signal.direction === "risk" ? "text-danger" : "text-success",
                )}
              >
                {signal.direction}
              </span>
            </div>
            <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
              {signal.rationale}
            </p>
          </div>
          <span className="label shrink-0">~{signal.horizon_days}d</span>
        </li>
      ))}
    </ul>
  );
}

/** Churn risk over time. A sparkline, drawn as SVG: no chart library for eight points. */
export function RiskSparkline({
  series,
  height = 34,
  width = 160,
}: {
  series: SignalPoint[];
  height?: number;
  width?: number;
}) {
  if (series.length < 2) {
    return <span className="label">not enough history</span>;
  }
  const points = series.map((point, index) => {
    const x = (index / (series.length - 1)) * width;
    const y = height - point.churn_risk * height;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  const last = series[series.length - 1]!;
  const first = series[0]!;
  const rising = last.churn_risk > first.churn_risk;

  return (
    <svg width={width} height={height} className="shrink-0" role="img" aria-label="Churn risk over time">
      <polyline
        points={points.join(" ")}
        fill="none"
        stroke={rising ? "hsl(var(--danger))" : "hsl(var(--success))"}
        strokeWidth="1.5"
      />
      <circle
        cx={width}
        cy={height - last.churn_risk * height}
        r="2.5"
        fill={rising ? "hsl(var(--danger))" : "hsl(var(--success))"}
      />
    </svg>
  );
}

export function ForecastCard({ report }: { report: SignalReport }) {
  return (
    <Card>
      <CardHeader className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <CardTitle>Forecast</CardTitle>
          <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{report.headline}</p>
        </div>
        <TrajectoryBadge trajectory={report.trajectory} />
      </CardHeader>
      <CardContent className="grid gap-4 sm:grid-cols-3">
        <Metric label="Churn risk" value={`${Math.round(report.churn_risk * 100)}%`} tone="danger" />
        <Metric
          label="Expansion"
          value={`${Math.round(report.expansion_score * 100)}%`}
          tone="success"
        />
        <Metric
          label="Confidence"
          value={`${Math.round(report.confidence * 100)}%`}
          hint={`${report.signals.length} signal${report.signals.length === 1 ? "" : "s"}`}
        />
      </CardContent>
      {report.series.length > 1 && (
        <div className="flex items-center gap-4 border-t border-border px-5 py-3">
          <span className="label">Risk, last {report.series.length} days</span>
          <RiskSparkline series={report.series} />
        </div>
      )}
    </Card>
  );
}

function Metric({
  label,
  value,
  tone,
  hint,
}: {
  label: string;
  value: string;
  tone?: "danger" | "success";
  hint?: string;
}) {
  return (
    <div>
      <p className="label">{label}</p>
      <p
        className={cn(
          "figure mt-1.5 text-[1.75rem]",
          tone === "danger" && "text-danger",
          tone === "success" && "text-success",
        )}
      >
        {value}
      </p>
      {hint && <p className="label mt-1.5">{hint}</p>}
    </div>
  );
}

export function RecommendationList({
  recommendations,
  customerId,
}: {
  recommendations: Recommendation[];
  customerId?: string;
}) {
  if (!recommendations.length) {
    return (
      <EmptyState
        title="Nothing needs doing"
        description="No open problems, no risk signals and no stalled goals for this customer."
      />
    );
  }
  return (
    <ul className="divide-y divide-border">
      {recommendations.map((item) => (
        <li key={item.key} className="px-5 py-4">
          <div className="flex flex-wrap items-center gap-2">
            <span
              className={cn(
                "rounded-sm border px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-label",
                PRIORITY_STYLES[item.priority],
              )}
            >
              {item.priority.replace(/_/g, " ")}
            </span>
            <span className="label">{item.category.replace(/_/g, " ")}</span>
          </div>
          <p className="mt-2 text-sm font-semibold">{item.action}</p>
          <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{item.rationale}</p>
          {item.playbook.length > 0 && (
            <ul className="mt-2.5 space-y-1 border-l-2 border-border pl-3">
              {item.playbook.map((step) => (
                <li key={step} className="text-xs leading-relaxed text-muted-foreground">
                  {step}
                </li>
              ))}
            </ul>
          )}
          {customerId && item.memory_ids.length > 0 && (
            <Link
              href={`/customers/${customerId}`}
              className="label mt-2.5 inline-block hover:text-accent"
            >
              {item.memory_ids.length} memor{item.memory_ids.length === 1 ? "y" : "ies"} behind this
            </Link>
          )}
        </li>
      ))}
    </ul>
  );
}

export function GoalList({
  goals,
  onOverride,
  pendingId,
}: {
  goals: Goal[];
  onOverride?: (goal: Goal, status: GoalStatus) => void;
  pendingId?: string | null;
}) {
  if (!goals.length) {
    return (
      <EmptyState
        title="No goals recorded"
        description="Goals appear when a customer says what they are trying to do."
      />
    );
  }
  return (
    <ul className="divide-y divide-border">
      {goals.map((goal) => (
        <li key={goal.id} className="px-5 py-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0 flex-1">
              <p className="text-sm font-semibold">{goal.statement}</p>
              <p className="label mt-1.5">
                stated {formatRelative(goal.opened_at)}
                {goal.closed_at ? ` · closed ${formatRelative(goal.closed_at)}` : ""}
                {goal.overridden ? " · set by a person" : ""}
              </p>
            </div>
            <GoalStatusBadge status={goal.status} />
          </div>

          <div className="mt-3 flex items-center gap-3">
            <div className="h-1.5 w-full max-w-xs overflow-hidden rounded-full border border-border bg-surface-3">
              <div
                className={cn(
                  "h-full",
                  goal.status === "achieved"
                    ? "bg-success"
                    : goal.status === "stalled"
                      ? "bg-warning"
                      : "bg-accent",
                )}
                style={{ width: `${Math.round(goal.progress * 100)}%` }}
              />
            </div>
            <span className="numeric label">{Math.round(goal.progress * 100)}%</span>
          </div>

          {goal.evidence.length > 0 && (
            <details className="mt-3">
              <summary className="label cursor-pointer select-none hover:text-accent">
                {goal.evidence.length} piece{goal.evidence.length === 1 ? "" : "s"} of evidence
              </summary>
              <ul className="mt-2 space-y-1.5 border-l-2 border-border pl-3">
                {goal.evidence.map((entry, index) => (
                  <li key={`${entry.kind}-${index}`} className="text-xs text-muted-foreground">
                    <span className="label-strong mr-2">{entry.kind}</span>
                    {entry.cue ? `“${entry.cue}”` : entry.note || "—"}
                    {entry.at ? ` · ${formatRelative(entry.at)}` : ""}
                  </li>
                ))}
              </ul>
            </details>
          )}

          {onOverride && (
            <div className="mt-3 flex flex-wrap gap-px overflow-hidden rounded-md border border-border bg-border">
              {(["open", "progressing", "achieved", "abandoned"] as GoalStatus[]).map((status) => (
                <button
                  key={status}
                  disabled={pendingId === goal.id || goal.status === status}
                  onClick={() => onOverride(goal, status)}
                  className={cn(
                    "px-2.5 py-1 font-mono text-[10px] uppercase tracking-label transition-colors",
                    goal.status === status
                      ? "bg-accent text-accent-foreground"
                      : "bg-surface text-muted-foreground hover:bg-surface-2 hover:text-foreground",
                    "disabled:cursor-not-allowed disabled:opacity-60",
                  )}
                >
                  {status}
                </button>
              ))}
            </div>
          )}
        </li>
      ))}
    </ul>
  );
}
