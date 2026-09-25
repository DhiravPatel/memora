"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { EmptyState, ErrorState, LoadingRow } from "@/components/ui/states";
import { api } from "@/lib/api";
import { formatDate, formatRelative } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { Change, CustomerAt, CustomerChanges } from "@/lib/types";

const PERIODS = [
  { value: "last_session", label: "Since last conversation" },
  { value: "7d", label: "7 days" },
  { value: "30d", label: "30 days" },
  { value: "90d", label: "90 days" },
];

const WITHHELD = "[withheld]";

// Good news, bad news, and everything else — read from what happened, not from the type:
// a resolved problem is good news about a problem.
const BAD = new Set([
  "problem:opened",
  "problem:recurring",
  "intent:expressed",
  "subscription:cancelled",
  "health:fell",
  "risk:rose",
  "activity:quiet",
  "activity:fell",
  "feedback:negative",
  "feedback:rose",
  "goal:stalled",
  "goal:abandoned",
  "signal:started",
  "memory:outdated",
]);
const GOOD = new Set([
  "problem:resolved",
  "goal:achieved",
  "health:rose",
  "risk:fell",
  "activity:rose",
  "feedback:positive",
  "feedback:fell",
  "signal:stopped",
]);

function tone(change: Change): string {
  const key = `${change.type}:${change.kind}`;
  if (key === "health:crossed") {
    const after = String(change.after ?? "");
    return after.startsWith("healthy")
      ? "border-success/70 bg-success/10 text-success"
      : "border-danger/70 bg-danger/10 text-danger";
  }
  if (key === "lifecycle:moved") {
    return String(change.after ?? "").includes("risk") || change.after === "churned"
      ? "border-danger/70 bg-danger/10 text-danger"
      : "border-info/70 bg-info/10 text-info";
  }
  if (BAD.has(key)) return "border-danger/70 bg-danger/10 text-danger";
  if (GOOD.has(key)) return "border-success/70 bg-success/10 text-success";
  if (change.type === "subscription") return "border-violet/70 bg-violet/10 text-violet";
  return "";
}

/** What changed about a customer in a window, and what they looked like then and now (§26 4.1). */
export function ChangesPanel({
  projectId,
  customerId,
}: {
  projectId: string | null;
  customerId: string;
}) {
  const [since, setSince] = useState("30d");
  const [order, setOrder] = useState<"time" | "importance">("time");
  const [type, setType] = useState<string | null>(null);
  const changes = useQuery({
    queryKey: ["customer-changes", projectId, customerId, since, order],
    queryFn: () =>
      api<CustomerChanges>(
        `/v1/projects/${projectId}/customers/${customerId}/changes?since=${encodeURIComponent(since)}&order=${order}&limit=200`,
      ),
    enabled: Boolean(projectId),
  });

  const body = changes.data;
  const shown = (body?.changes ?? []).filter((change) => !type || change.type === type);

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <CardTitle>What changed</CardTitle>
              <p className="mt-1 text-xs text-muted-foreground">
                Read from the memories, goals, lifecycle and snapshots themselves — each change
                cites the record it came from.
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              {PERIODS.map((period) => (
                <button
                  key={period.value}
                  onClick={() => setSince(period.value)}
                  className={cn(
                    "border px-3 py-1.5 font-mono text-[10px] uppercase tracking-label transition-colors",
                    since === period.value
                      ? "border-accent bg-accent text-accent-foreground"
                      : "border-border bg-surface text-muted-foreground hover:border-accent hover:text-foreground",
                  )}
                >
                  {period.label}
                </button>
              ))}
              <Input
                type="date"
                aria-label="Since a date"
                className="h-8 w-40 text-[11px]"
                value={/^\d{4}-\d{2}-\d{2}$/.test(since) ? since : ""}
                max={new Date().toISOString().slice(0, 10)}
                onChange={(event) => event.target.value && setSince(event.target.value)}
              />
            </div>
          </div>
        </CardHeader>
        <CardContent>
          {changes.isLoading && <LoadingRow />}
          {changes.error && <ErrorState error={changes.error} />}
          {body && (
            <div className="space-y-2">
              <p className="font-display text-lg leading-snug text-foreground">{body.summary}</p>
              <p className="label">
                {formatDate(body.window.since)} →{" "}
                {body.now.live ? "now" : formatDate(body.window.until)}
                {" · "}
                {body.total} change{body.total === 1 ? "" : "s"}
              </p>
              {body.window.note && <p className="text-xs text-warning">{body.window.note}</p>}
              {body.withheld > 0 && (
                <p className="text-xs text-muted-foreground">
                  {body.withheld} change
                  {body.withheld === 1 ? " concerns a memory" : "s concern memories"} you may not
                  read.
                </p>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      {body && <ThenAndNow then={body.then} now={body.now} />}

      {body && (
        <Card>
          <CardHeader>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex flex-wrap gap-1.5">
                <button
                  onClick={() => setType(null)}
                  className={cn(
                    "label border px-2 py-1",
                    !type ? "border-accent text-foreground" : "border-border",
                  )}
                >
                  all · {body.total}
                </button>
                {Object.entries(body.counts)
                  .sort((a, b) => b[1] - a[1])
                  .map(([name, count]) => (
                    <button
                      key={name}
                      onClick={() => setType(type === name ? null : name)}
                      className={cn(
                        "label border px-2 py-1",
                        type === name ? "border-accent text-foreground" : "border-border",
                      )}
                    >
                      {name} · {count}
                    </button>
                  ))}
              </div>
              <div className="flex gap-1">
                {(["time", "importance"] as const).map((value) => (
                  <button
                    key={value}
                    onClick={() => setOrder(value)}
                    className={cn(
                      "label border px-2 py-1",
                      order === value ? "border-accent text-foreground" : "border-border",
                    )}
                  >
                    {value === "time" ? "newest first" : "most important first"}
                  </button>
                ))}
              </div>
            </div>
          </CardHeader>
          {shown.length === 0 ? (
            <EmptyState
              title="Nothing material changed"
              description="No problems, plan or preference changes, lifecycle moves, goal progress, health shifts or activity swings in this window."
            />
          ) : (
            <ul>
              {shown.map((change, index) => (
                <ChangeRow
                  key={`${change.type}-${change.kind}-${change.detected_at}-${index}`}
                  change={change}
                />
              ))}
            </ul>
          )}
        </Card>
      )}
    </div>
  );
}

function ChangeRow({ change }: { change: Change }) {
  const before = change.before === WITHHELD ? "withheld" : change.before;
  // The title already says it for most changes; before → after is worth a line when the
  // earlier record is not in the title.
  const showBefore = Boolean(before) && !change.title.includes(String(change.before));
  return (
    <li className="flex gap-4 border-t border-border px-5 py-3">
      <div className="w-28 shrink-0 pt-0.5">
        <span className="label" title={formatDate(change.detected_at)}>
          {formatRelative(change.detected_at)}
        </span>
      </div>
      <div className="min-w-0 flex-1 space-y-1.5">
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge className={tone(change)}>
            {change.type} · {change.kind}
          </Badge>
          {change.track && change.track !== "lifecycle" && (
            <Badge>{change.track.replace(/_/g, " ")}</Badge>
          )}
          {change.source === "snapshot" && (
            <Badge title="Compared from the snapshots at each end">snapshot</Badge>
          )}
        </div>
        <p className="text-sm leading-snug text-foreground">{change.title}</p>
        {showBefore && (
          <p className="text-xs text-muted-foreground">
            <span className={cn(change.before !== WITHHELD && "line-through")}>{before}</span>
            {change.after && change.after !== change.title && <span> → {change.after}</span>}
          </p>
        )}
        {change.reasons.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {change.reasons.map((reason) => (
              <Badge key={reason} className="normal-case tracking-normal">
                {reason}
              </Badge>
            ))}
          </div>
        )}
      </div>
    </li>
  );
}

const FIELDS: {
  key: string;
  label: string;
  render?: (value: unknown, state: Record<string, unknown>) => string;
}[] = [
  {
    key: "plan",
    label: "Plan",
    render: (value) => (value ? String(value) : "—"),
  },
  {
    key: "health_band",
    label: "Health",
    render: (value, state) =>
      value
        ? `${String(value).replace(/_/g, " ")}${typeof state.health_score === "number" ? ` (${Math.round(state.health_score)})` : ""}`
        : "—",
  },
  { key: "lifecycle", label: "Lifecycle" },
  { key: "trajectory", label: "Heading" },
  {
    key: "churn_risk",
    label: "Churn risk",
    render: (value) => (typeof value === "number" ? `${Math.round(value * 100)}%` : "—"),
  },
  { key: "open_problems", label: "Open problems" },
  { key: "open_goals", label: "Open goals" },
  { key: "stalled_goals", label: "Stalled goals" },
  { key: "achieved_goals", label: "Goals achieved" },
  { key: "preferred_channel", label: "Preferred channel" },
  { key: "intents", label: "Intents" },
  { key: "signals", label: "Signals" },
];

function show(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (value === WITHHELD) return "withheld";
  if (Array.isArray(value))
    return value.length ? value.map((item) => String(item).replace(/_/g, " ")).join(", ") : "none";
  return String(value).replace(/_/g, " ");
}

function ThenAndNow({ then, now }: { then: CustomerAt; now: CustomerAt }) {
  const before = then.state;
  const after = now.state ?? {};
  const tracks = Array.from(
    new Set([
      ...Object.keys((before?.tracks as Record<string, unknown>) ?? {}),
      ...Object.keys((after.tracks as Record<string, unknown>) ?? {}),
    ]),
  ).filter((track) => track !== "lifecycle");
  const rows = [
    ...FIELDS.map((field) => ({
      label: field.label,
      then: before
        ? field.render
          ? field.render(before[field.key], before)
          : show(before[field.key])
        : "—",
      now: field.render ? field.render(after[field.key], after) : show(after[field.key]),
    })),
    ...tracks.map((track) => ({
      label: track.replace(/_/g, " "),
      then: before ? show((before.tracks as Record<string, unknown>)?.[track]) : "—",
      now: show((after.tracks as Record<string, unknown>)?.[track]),
    })),
  ];
  return (
    <Card>
      <CardHeader>
        <CardTitle>Then and now</CardTitle>
        <p className="mt-1 text-xs text-muted-foreground">
          {before
            ? `Then is the snapshot of ${formatDate(then.taken_at)}; now is ${now.live ? "computed live" : `the snapshot of ${formatDate(now.taken_at)}`}.`
            : "Nothing was recorded about this customer before the window began."}
        </p>
      </CardHeader>
      <div className="grid grid-cols-[minmax(8rem,1fr)_2fr_2fr] border-t border-border text-xs">
        <span className="label border-b border-border px-5 py-2" />
        <span className="label border-b border-border px-5 py-2">Then</span>
        <span className="label border-b border-border px-5 py-2">Now</span>
        {rows.map((row) => {
          const moved = before !== null && row.then !== row.now;
          return (
            <div key={row.label} className="contents">
              <span className="label border-b border-border px-5 py-2">{row.label}</span>
              <span
                className={cn(
                  "border-b border-border px-5 py-2",
                  moved ? "text-muted-foreground line-through" : "text-foreground",
                )}
              >
                {row.then}
              </span>
              <span
                className={cn(
                  "border-b border-border px-5 py-2",
                  moved ? "font-medium text-accent" : "text-foreground",
                )}
              >
                {row.now}
              </span>
            </div>
          );
        })}
      </div>
    </Card>
  );
}
