"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { GoalStatusBadge, TrajectoryBadge } from "@/components/foresight";
import { FreshnessBadge } from "@/components/freshness";
import { HealthBadge } from "@/components/health-card";
import { StateBadge } from "@/components/lifecycle";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState, ErrorState, LoadingRow } from "@/components/ui/states";
import { api } from "@/lib/api";
import { formatDate, formatDay, formatRelative } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { CustomerBrief } from "@/lib/types";

const PERIODS = [
  { value: "last_view", label: "Since I last looked" },
  { value: "last_session", label: "Since last conversation" },
  { value: "7d", label: "7 days" },
  { value: "30d", label: "30 days" },
];

const WITHHELD = "[withheld]";

function ago(days: number | null): string {
  if (days === null) return "—";
  if (days === 0) return "today";
  if (days === 1) return "1 day";
  if (days < 14) return `${days} days`;
  if (days < 60) return `${Math.floor(days / 7)} weeks`;
  return `${Math.floor(days / 30)} months`;
}

/** Decision-ready: the situation, what to raise, what not to do and why (§26 5.2). */
export function CustomerBriefPanel({
  projectId,
  customerId,
  onOpenTab,
}: {
  projectId: string | null;
  customerId: string;
  onOpenTab?: (tab: "Changes" | "State" | "Agents" | "Goals" | "Freshness") => void;
}) {
  // A person reading the brief wants what changed since *they* last looked (§26 4.1).
  const [since, setSince] = useState("last_view");
  const [copied, setCopied] = useState(false);
  const brief = useQuery({
    queryKey: ["customer-brief", projectId, customerId, since],
    queryFn: () =>
      api<CustomerBrief>(`/v1/projects/${projectId}/customers/${customerId}/brief`, {
        query: { since },
      }),
    enabled: Boolean(projectId),
  });

  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 2000);
    return () => clearTimeout(timer);
  }, [copied]);

  const body = brief.data;

  async function copy() {
    if (!body) return;
    await navigator.clipboard.writeText(body.markdown);
    setCopied(true);
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <CardTitle>Brief</CardTitle>
              <p className="mt-1 text-xs text-muted-foreground">
                What to raise, what not to do and why — judged from this customer&apos;s own history
                and your guardrails, each point with the evidence behind it.
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
              <Button size="sm" variant="secondary" onClick={copy} disabled={!body}>
                {copied ? "Copied" : "Copy as Markdown"}
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          {brief.isLoading && <LoadingRow />}
          {brief.error && <ErrorState error={brief.error} />}
          {body && (
            <div className="space-y-2">
              <p className="font-display text-xl leading-snug text-foreground">{body.headline}</p>
              <p className="label">
                {body.recent_changes.window.label} · generated {formatRelative(body.generated_at)}
                {body.customer.customer_since &&
                  ` · customer since ${formatDay(body.customer.customer_since)}`}
              </p>
              {body.withheld > 0 && (
                <p className="text-xs text-muted-foreground">
                  {body.withheld} memor{body.withheld === 1 ? "y is" : "ies are"} withheld from this
                  brief for you; counts still include them.
                </p>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      {body && (
        <div className="grid gap-6 lg:grid-cols-[3fr_2fr]">
          <div className="space-y-6">
            <TalkAbout brief={body} />
            <Drift brief={body} onOpenFreshness={() => onOpenTab?.("Freshness")} />
            <Cautions brief={body} onOpenAgents={() => onOpenTab?.("Agents")} />
            <OpenIssues brief={body} />
            <WhatChanged brief={body} onOpenChanges={() => onOpenTab?.("Changes")} />
          </div>
          <div className="space-y-6">
            <Situation brief={body} onOpenState={() => onOpenTab?.("State")} />
            <CaresAbout brief={body} />
            <NextStep brief={body} />
            <Preferences brief={body} />
            <Goals brief={body} onOpenGoals={() => onOpenTab?.("Goals")} />
            <Signals brief={body} />
            <LastConversation brief={body} />
          </div>
        </div>
      )}
    </div>
  );
}

function TalkAbout({ brief }: { brief: CustomerBrief }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Talk about</CardTitle>
        <p className="mt-1 text-xs text-muted-foreground">Most important first.</p>
      </CardHeader>
      {brief.talking_points.length === 0 ? (
        <EmptyState
          title="Nothing pressing"
          description="No open problems, intents, stalled goals or changes to raise."
        />
      ) : (
        <ol>
          {brief.talking_points.map((point, index) => (
            <li key={point} className="flex gap-4 border-t border-border px-5 py-3">
              <span className="numeric w-5 shrink-0 pt-0.5 text-xs text-muted-foreground">
                {index + 1}
              </span>
              <p className="text-sm leading-snug text-foreground">{point}</p>
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}

function Drift({ brief, onOpenFreshness }: { brief: CustomerBrief; onOpenFreshness: () => void }) {
  const flags = brief.drift ?? [];
  if (flags.length === 0) return null;
  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <CardTitle>Possibly out of date</CardTitle>
            <p className="mt-1 text-xs text-muted-foreground">
              Evidence since says otherwise. Ask before relying on it; nothing was changed.
            </p>
          </div>
          <button onClick={onOpenFreshness} className="label hover:text-accent">
            Review →
          </button>
        </div>
      </CardHeader>
      <ul>
        {flags.map((flag) => (
          <li
            key={flag.id}
            className="space-y-1 border-t border-l-2 border-t-border border-l-danger px-5 py-3"
          >
            <Badge className="border-danger/70 bg-danger/10 text-danger">{flag.kind_label}</Badge>
            <p className="text-sm leading-snug text-foreground">{flag.summary}</p>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function Cautions({ brief, onOpenAgents }: { brief: CustomerBrief; onOpenAgents: () => void }) {
  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <CardTitle>Don&apos;t</CardTitle>
            <p className="mt-1 text-xs text-muted-foreground">
              What your guardrails would say to each action right now — nothing is recorded.
            </p>
          </div>
          <button onClick={onOpenAgents} className="label hover:text-accent">
            Try an action →
          </button>
        </div>
      </CardHeader>
      {brief.cautions.length === 0 ? (
        <EmptyState
          title="No cautions"
          description="No rule stands in the way of selling, contacting or closing for this customer."
        />
      ) : (
        <ul>
          {brief.cautions.map((caution) => (
            <li
              key={caution.text}
              className={cn(
                "space-y-1.5 border-t border-l-2 border-t-border px-5 py-3",
                caution.decision === "deny" ? "border-l-danger" : "border-l-warning",
              )}
            >
              <p className="text-sm leading-snug text-foreground">{caution.text}</p>
              <div className="flex flex-wrap gap-1">
                <Badge
                  className={
                    caution.decision === "deny"
                      ? "border-danger/70 bg-danger/10 text-danger"
                      : "border-warning/70 bg-warning/10 text-warning"
                  }
                >
                  {caution.decision === "deny" ? "refused" : "needs a person"}
                </Badge>
                {caution.actions.map((action) => (
                  <Badge key={action}>{action.replace(/_/g, " ")}</Badge>
                ))}
                {caution.evidence.length > 0 && (
                  <Badge title={caution.evidence.join(", ")}>
                    {caution.evidence.length} memor{caution.evidence.length === 1 ? "y" : "ies"}
                  </Badge>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function OpenIssues({ brief }: { brief: CustomerBrief }) {
  const hidden = brief.situation.open_problems - brief.open_issues.length;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Open issues</CardTitle>
        <p className="mt-1 text-xs text-muted-foreground">
          {brief.situation.open_problems} open
          {hidden > 0 && ` · ${hidden} not shown`}
        </p>
      </CardHeader>
      {brief.open_issues.length === 0 ? (
        <EmptyState title="Nothing open" description="No unresolved problems in memory." />
      ) : (
        <ul>
          {brief.open_issues.map((issue) => (
            <li key={issue.id} className="flex gap-4 border-t border-border px-5 py-3">
              <p className="min-w-0 flex-1 text-sm leading-snug text-foreground">
                {issue.content === WITHHELD ? "Withheld" : issue.content}
              </p>
              <div className="flex shrink-0 flex-col items-end gap-1">
                <span className="label">
                  {issue.age_days === 0 ? "opened today" : `open ${ago(issue.age_days)}`}
                </span>
                {issue.times_reported > 1 && (
                  <Badge className="border-danger/70 bg-danger/10 text-danger">
                    reported {issue.times_reported}×
                  </Badge>
                )}
                <FreshnessBadge state={issue.freshness} />
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function WhatChanged({
  brief,
  onOpenChanges,
}: {
  brief: CustomerBrief;
  onOpenChanges: () => void;
}) {
  const changes = brief.recent_changes;
  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <CardTitle>What changed</CardTitle>
            <p className="mt-1 text-xs text-muted-foreground">{changes.summary}</p>
            {changes.window.note && (
              <p className="mt-1 text-xs text-warning">{changes.window.note}</p>
            )}
          </div>
          <button onClick={onOpenChanges} className="label hover:text-accent">
            All {changes.total} →
          </button>
        </div>
      </CardHeader>
      {changes.items.length > 0 && (
        <ul>
          {changes.items.map((item, index) => (
            <li
              key={`${item.type}-${item.kind}-${item.detected_at}-${index}`}
              className="flex gap-4 border-t border-border px-5 py-2.5"
            >
              <span className="label w-24 shrink-0 pt-0.5" title={formatDate(item.detected_at)}>
                {formatRelative(item.detected_at)}
              </span>
              <div className="min-w-0 flex-1">
                <p className="text-sm leading-snug text-foreground">{item.title}</p>
                {item.reasons.length > 0 && (
                  <p className="mt-0.5 text-xs text-muted-foreground">{item.reasons.join("; ")}</p>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function Situation({ brief, onOpenState }: { brief: CustomerBrief; onOpenState: () => void }) {
  const { health, plan, lifecycle, goals } = brief.situation;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Situation</CardTitle>
      </CardHeader>
      <dl className="grid grid-cols-[7rem_1fr] border-t border-border text-xs">
        <dt className="label border-b border-border px-5 py-2.5">Health</dt>
        <dd className="flex flex-wrap items-center gap-2 border-b border-border px-5 py-2.5">
          <span className="numeric text-foreground">{Math.round(health.score)}/100</span>
          <HealthBadge band={health.band} />
          <TrajectoryBadge trajectory={health.trajectory} />
          <span className="text-muted-foreground">
            {Math.round(health.churn_risk * 100)}% churn risk
          </span>
        </dd>
        <dt className="label border-b border-border px-5 py-2.5">Plan</dt>
        <dd className="space-y-1 border-b border-border px-5 py-2.5">
          <p className="text-foreground">
            {plan.name ? `${plan.name.replace(/\b\w/g, (letter) => letter.toUpperCase())}` : "—"}
            {plan.direction && (
              <span className="text-muted-foreground"> · {plan.direction.replace(/_/g, " ")}</span>
            )}
          </p>
          {plan.statement && <p className="text-muted-foreground">{plan.statement}</p>}
        </dd>
        <dt className="label border-b border-border px-5 py-2.5">Lifecycle</dt>
        <dd className="space-y-1.5 border-b border-border px-5 py-2.5">
          {lifecycle.length === 0 && <span className="text-muted-foreground">not tracked</span>}
          {lifecycle.map((track) => {
            // Why they are there now; the reasons they entered it only when it cannot be
            // re-checked (set by a person, or the initial state).
            const now = track.reasons_now ?? track.reasons;
            const entered = track.reasons.join("; ");
            return (
              <button
                key={track.track}
                onClick={onOpenState}
                className="flex w-full flex-wrap items-center gap-2 text-left"
                title={
                  entered
                    ? `Entered ${formatDate(track.entered_at)} because: ${entered}`
                    : undefined
                }
              >
                <span className="label">{track.label}</span>
                <StateBadge state={track.state} pinned={track.pinned} />
                {track.moving_to && (
                  <span
                    className="text-muted-foreground"
                    title="Nothing keeps them in this state now; the next refresh moves them"
                  >
                    moving to {track.moving_to.replace(/_/g, " ")}
                  </span>
                )}
                {!track.moving_to && now.length > 0 && (
                  <span className="text-muted-foreground">
                    {track.holds === false && track.held_by
                      ? `not yet ${track.held_by.replace(/_/g, " ")}: `
                      : ""}
                    {now.slice(0, 2).join("; ")}
                  </span>
                )}
              </button>
            );
          })}
        </dd>
        <dt className="label border-b border-border px-5 py-2.5">Goals</dt>
        <dd className="border-b border-border px-5 py-2.5 text-muted-foreground">
          {Object.entries(goals)
            .filter(([, count]) => count > 0)
            .map(([status, count]) => `${count} ${status}`)
            .join(" · ") || "none stated"}
        </dd>
        <dt className="label px-5 py-2.5">Why</dt>
        <dd className="space-y-0.5 px-5 py-2.5 text-foreground">
          {(brief.situation.why ?? []).length === 0 && (
            <span className="text-muted-foreground">nothing stands out</span>
          )}
          {(brief.situation.why ?? []).map((reason) => (
            <p key={reason}>{reason}</p>
          ))}
        </dd>
      </dl>
    </Card>
  );
}

function CaresAbout({ brief }: { brief: CustomerBrief }) {
  const topics = brief.cares_about ?? [];
  if (topics.length === 0) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle>They care about</CardTitle>
      </CardHeader>
      <ul>
        {topics.map((item) => (
          <li
            key={item.topic}
            className="flex items-baseline justify-between gap-3 border-t border-border px-5 py-2.5"
          >
            <span className="text-sm text-foreground">{item.topic}</span>
            <span className="label shrink-0">{item.detail}</span>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function NextStep({ brief }: { brief: CustomerBrief }) {
  const step = brief.next_step;
  if (!step && brief.set_aside.length === 0) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Next step</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {step ? (
          <div className="space-y-1.5">
            <p className="text-sm font-medium leading-snug text-foreground">{step.action}</p>
            <p className="text-xs text-muted-foreground">{step.rationale}</p>
            {step.playbook.length > 0 && (
              <ul className="list-disc space-y-0.5 pl-4 text-xs text-muted-foreground">
                {step.playbook.map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
            )}
          </div>
        ) : (
          <p className="text-xs text-muted-foreground">
            Every recommendation is ruled out by a caution.
          </p>
        )}
        {brief.set_aside.map((item) => (
          <p
            key={item.key}
            className="border-l-2 border-warning pl-3 text-xs text-muted-foreground"
          >
            Set aside: <span className="line-through">{item.action}</span> — {item.because}
          </p>
        ))}
      </CardContent>
    </Card>
  );
}

function Preferences({ brief }: { brief: CustomerBrief }) {
  const { channel, opt_outs, statements } = brief.preferences;
  if (!channel && opt_outs.length === 0 && statements.length === 0) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle>What they prefer</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        <div className="flex flex-wrap gap-1.5">
          {channel && (
            <Badge className="border-info/70 bg-info/10 text-info">prefers {channel}</Badge>
          )}
          {opt_outs.map((item) => (
            <Badge
              key={item.kind}
              className="border-danger/70 bg-danger/10 normal-case tracking-normal text-danger"
            >
              {item.words}
            </Badge>
          ))}
        </div>
        {statements.map((item) => (
          <p key={item.id} className="text-xs leading-snug text-muted-foreground">
            “{item.content}”
          </p>
        ))}
      </CardContent>
    </Card>
  );
}

function Goals({ brief, onOpenGoals }: { brief: CustomerBrief; onOpenGoals: () => void }) {
  if (brief.goals.length === 0) return null;
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-3">
          <CardTitle>Goals</CardTitle>
          <button onClick={onOpenGoals} className="label hover:text-accent">
            All goals →
          </button>
        </div>
      </CardHeader>
      <ul>
        {brief.goals.map((goal) => (
          <li key={goal.id} className="flex items-start gap-3 border-t border-border px-5 py-2.5">
            <p className="min-w-0 flex-1 text-sm leading-snug text-foreground">{goal.statement}</p>
            <GoalStatusBadge status={goal.status} />
          </li>
        ))}
      </ul>
    </Card>
  );
}

function Signals({ brief }: { brief: CustomerBrief }) {
  const signals = [...brief.risks, ...brief.opportunities];
  if (signals.length === 0) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Signals</CardTitle>
      </CardHeader>
      <ul>
        {signals.map((signal) => (
          <li key={signal.key} className="space-y-0.5 border-t border-border px-5 py-2.5">
            <div className="flex items-center gap-2">
              <Badge
                className={
                  signal.direction === "risk"
                    ? "border-danger/70 bg-danger/10 text-danger"
                    : "border-success/70 bg-success/10 text-success"
                }
              >
                {signal.direction}
              </Badge>
              <span className="text-sm text-foreground">{signal.label}</span>
            </div>
            <p className="text-xs text-muted-foreground">{signal.rationale}</p>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function LastConversation({ brief }: { brief: CustomerBrief }) {
  const conversation = brief.last_conversation;
  if (!conversation) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Last conversation</CardTitle>
        <p className="mt-1 text-xs text-muted-foreground">
          {[conversation.agent, conversation.closed_at && formatRelative(conversation.closed_at)]
            .filter(Boolean)
            .join(" · ")}
        </p>
      </CardHeader>
      <CardContent>
        <p className="text-sm leading-snug text-foreground">
          {conversation.summary === WITHHELD
            ? "The summary quotes something you may not read."
            : conversation.summary}
        </p>
      </CardContent>
    </Card>
  );
}
