"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import { StateBadge } from "@/components/lifecycle";
import { Badge, MemoryTypeBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState, ErrorState, LoadingRow } from "@/components/ui/states";
import { api, apiText } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { CustomerJourney, Milestone, MilestoneCategory } from "@/lib/types";

const WINDOWS = [
  { value: "", label: "All time" },
  { value: "180d", label: "6 months" },
  { value: "90d", label: "90 days" },
  { value: "30d", label: "30 days" },
];

const CATEGORY_WORDS: Record<MilestoneCategory, string> = {
  account: "account",
  usage: "adoption",
  problem: "problems",
  plan: "plan",
  intent: "intents",
  preference: "preferences",
  goal: "goals",
  health: "health",
  lifecycle: "lifecycle",
  activity: "activity",
  feedback: "feedback",
  relationship: "relationships",
};

const DOT: Record<Milestone["tone"], string> = {
  negative: "border-danger bg-danger",
  positive: "border-success bg-success",
  neutral: "border-border-strong bg-surface",
};

const TONE_BADGE: Record<Milestone["tone"], string> = {
  negative: "border-danger/70 bg-danger/10 text-danger",
  positive: "border-success/70 bg-success/10 text-success",
  neutral: "",
};

// "Key moments" keeps the milestones that matter most: plan changes, first problems,
// band crossings into trouble, lifecycle moves, goals reached, silences.
const KEY_MOMENTS = 0.75;

function words(value: string | null | undefined): string {
  return String(value ?? "").replace(/_/g, " ");
}

function month(at: string): string {
  return new Date(at).toLocaleDateString(undefined, { month: "long", year: "numeric" });
}

function day(at: string): { day: string; month: string } {
  const date = new Date(at);
  return {
    day: date.toLocaleDateString(undefined, { day: "2-digit" }),
    month: date.toLocaleDateString(undefined, { month: "short" }),
  };
}

/** The customer's journey as milestones, not rows (§26 6.6). */
export function CustomerJourneyPanel({
  projectId,
  customerId,
}: {
  projectId: string | null;
  customerId: string;
}) {
  const [since, setSince] = useState("");
  const [category, setCategory] = useState<MilestoneCategory | null>(null);
  const [keyOnly, setKeyOnly] = useState(false);
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [copied, setCopied] = useState(false);
  const base = `/v1/projects/${projectId}/customers/${encodeURIComponent(customerId)}/journey`;
  const query = {
    since: since || undefined,
    min_importance: keyOnly ? KEY_MOMENTS : undefined,
    limit: 500,
  };

  const journey = useQuery({
    queryKey: ["customer-journey", projectId, customerId, since, keyOnly],
    queryFn: () => api<CustomerJourney>(base, { query }),
    enabled: Boolean(projectId),
  });

  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 2000);
    return () => clearTimeout(timer);
  }, [copied]);

  const body = journey.data;
  const shown = useMemo(
    () => (body?.milestones ?? []).filter((item) => !category || item.category === category),
    [body, category],
  );
  const months = useMemo(() => {
    const groups: { month: string; items: Milestone[] }[] = [];
    for (const item of shown) {
      const name = month(item.at);
      const last = groups[groups.length - 1];
      if (last && last.month === name) last.items.push(item);
      else groups.push({ month: name, items: [item] });
    }
    return groups;
  }, [shown]);

  async function copy() {
    const page = await apiText(base, {
      query: { ...query, format: "markdown", categories: category ?? undefined },
    });
    await navigator.clipboard.writeText(page);
    setCopied(true);
  }

  function toggle(id: string) {
    setOpen((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <CardTitle>Journey</CardTitle>
              <p className="mt-1 text-xs text-muted-foreground">
                Milestones, not rows — each opens to what happened, why it mattered, the memories it
                changed, what it did to health and the lifecycle move that followed.
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              {WINDOWS.map((window) => (
                <button
                  key={window.value || "all"}
                  onClick={() => setSince(window.value)}
                  className={cn(
                    "border px-3 py-1.5 font-mono text-[10px] uppercase tracking-label transition-colors",
                    since === window.value
                      ? "border-accent bg-accent text-accent-foreground"
                      : "border-border bg-surface text-muted-foreground hover:border-accent hover:text-foreground",
                  )}
                >
                  {window.label}
                </button>
              ))}
              <button
                onClick={() => setKeyOnly((value) => !value)}
                aria-pressed={keyOnly}
                className={cn(
                  "border px-3 py-1.5 font-mono text-[10px] uppercase tracking-label transition-colors",
                  keyOnly
                    ? "border-accent bg-accent text-accent-foreground"
                    : "border-border bg-surface text-muted-foreground hover:border-accent hover:text-foreground",
                )}
                title="Only the milestones that matter most"
              >
                Key moments
              </button>
              <Button size="sm" variant="outline" onClick={copy} disabled={!body}>
                {copied ? "Copied" : "Copy as Markdown"}
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          {journey.isLoading && <LoadingRow />}
          {journey.error && <ErrorState error={journey.error} />}
          {body && (
            <div className="space-y-2">
              <p className="font-display text-lg leading-snug text-foreground">{body.summary}</p>
              {body.window.note && <p className="text-xs text-warning">{body.window.note}</p>}
              {body.withheld > 0 && (
                <p className="text-xs text-muted-foreground">
                  {body.withheld} milestone
                  {body.withheld === 1 ? " concerns a memory" : "s concern memories"} you may not
                  read.
                </p>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      {body && (
        <Card>
          <CardHeader>
            <div className="flex flex-wrap gap-1.5">
              <button
                onClick={() => setCategory(null)}
                className={cn(
                  "label border px-2 py-1",
                  !category ? "border-accent text-foreground" : "border-border",
                )}
              >
                all · {body.total}
              </button>
              {(Object.entries(body.counts) as [MilestoneCategory, number][])
                .sort((a, b) => b[1] - a[1])
                .map(([name, count]) => (
                  <button
                    key={name}
                    onClick={() => setCategory(category === name ? null : name)}
                    className={cn(
                      "label border px-2 py-1",
                      category === name ? "border-accent text-foreground" : "border-border",
                    )}
                  >
                    {CATEGORY_WORDS[name] ?? name} · {count}
                  </button>
                ))}
            </div>
          </CardHeader>
          {shown.length === 0 ? (
            <EmptyState
              title="No milestones yet"
              description="Plan changes, first uses, problems and their repeats, goals, health crossing a band, lifecycle moves and long silences appear here as they happen."
            />
          ) : (
            <div>
              {months.map((group) => (
                <section key={group.month}>
                  <h3 className="label border-t border-border bg-surface-2 px-5 py-2">
                    {group.month}
                  </h3>
                  <ol>
                    {group.items.map((item) => (
                      <MilestoneRow
                        key={item.id}
                        item={item}
                        expanded={open.has(item.id)}
                        onToggle={() => toggle(item.id)}
                      />
                    ))}
                  </ol>
                </section>
              ))}
              {body.truncated && (
                <p className="border-t border-border px-5 py-2 text-[11px] text-muted-foreground">
                  Showing the {body.milestones.length} most important of {body.total} milestones.
                </p>
              )}
            </div>
          )}
        </Card>
      )}
    </div>
  );
}

function MilestoneRow({
  item,
  expanded,
  onToggle,
}: {
  item: Milestone;
  expanded: boolean;
  onToggle: () => void;
}) {
  const when = day(item.at);
  return (
    <li className="relative flex gap-4 border-t border-border px-5 py-3">
      <div className="w-12 shrink-0 text-right" title={formatDate(item.at)}>
        <div className="font-display text-lg leading-none text-foreground">{when.day}</div>
        <div className="label mt-0.5">{when.month}</div>
      </div>
      <div className="relative flex w-3 shrink-0 justify-center">
        <span className="absolute inset-y-[-12px] w-px bg-border" aria-hidden />
        <span
          className={cn("relative mt-1.5 h-2.5 w-2.5 rounded-full border-2", DOT[item.tone])}
          aria-hidden
        />
      </div>
      <div className="min-w-0 flex-1 space-y-1.5">
        <button
          onClick={onToggle}
          aria-expanded={expanded}
          className="flex w-full flex-wrap items-center gap-1.5 text-left"
        >
          <span className="text-sm font-medium leading-snug text-foreground">{item.title}</span>
          <Badge className={TONE_BADGE[item.tone]}>
            {CATEGORY_WORDS[item.category] ?? item.category}
          </Badge>
          {item.topics.map((topic) => (
            <Badge key={topic} className="normal-case tracking-normal">
              {topic}
            </Badge>
          ))}
          {item.recorded_at && (
            <Badge
              title={`Happened ${formatDate(item.at)}; recorded ${formatDate(item.recorded_at)}`}
            >
              recorded {formatDate(item.recorded_at)}
            </Badge>
          )}
          <span className="label ml-auto">{expanded ? "less" : "more"}</span>
        </button>
        <p className="text-xs leading-snug text-muted-foreground">{item.what_happened}</p>
        {expanded && <MilestoneDetail item={item} />}
      </div>
    </li>
  );
}

function MilestoneDetail({ item }: { item: Milestone }) {
  const health = item.health;
  const unchanged = Boolean(item.detail?.health_unchanged);
  return (
    <dl className="mt-2 grid grid-cols-1 gap-x-6 gap-y-3 border-l-2 border-border pl-4 text-xs md:grid-cols-2">
      <div>
        <dt className="label">What happened</dt>
        <dd className="mt-0.5 text-foreground">{item.what_happened}</dd>
      </div>
      <div>
        <dt className="label">Why it mattered</dt>
        <dd className="mt-0.5 text-foreground">{item.why_it_matters ?? "—"}</dd>
      </div>
      <div>
        <dt className="label">Memories that changed</dt>
        <dd className="mt-0.5 space-y-1">
          {item.memories.length === 0 && <span className="text-muted-foreground">none</span>}
          {item.memories.map((memory) => (
            <p key={memory.id} className="flex flex-wrap items-baseline gap-1.5 text-foreground">
              <MemoryTypeBadge type={memory.type} />
              <span className="min-w-0 flex-1">{memory.content ?? memory.id}</span>
              <span className="label">{memory.change}</span>
            </p>
          ))}
        </dd>
      </div>
      <div>
        <dt className="label">Health</dt>
        <dd className="mt-0.5 space-y-1 text-foreground">
          {health?.after ? (
            <>
              <p>
                {health.before?.score != null
                  ? `${Math.round(health.before.score)} (${words(health.before.band)})`
                  : "first measured"}
                {" → "}
                {health.after.score != null ? Math.round(health.after.score) : "—"} (
                {words(health.after.band)})
                {health.delta != null && health.delta !== 0 && (
                  <span
                    className={cn(
                      "ml-1.5 font-mono",
                      health.delta < 0 ? "text-danger" : "text-success",
                    )}
                  >
                    {health.delta > 0 ? "+" : ""}
                    {health.delta.toFixed(1)}
                  </span>
                )}
              </p>
              {health.drivers.length > 0 && (
                <p className="text-muted-foreground">With it: {health.drivers.join("; ")}</p>
              )}
            </>
          ) : (
            <span className="text-muted-foreground">
              {unchanged ? "No material change" : "Not measured at this moment"}
            </span>
          )}
        </dd>
      </div>
      <div>
        <dt className="label">State transition</dt>
        <dd className="mt-0.5 space-y-1">
          {item.transitions.length === 0 && <span className="text-muted-foreground">none</span>}
          {item.transitions.map((move) => (
            <div key={move.id} className="space-y-0.5">
              <p className="flex flex-wrap items-center gap-1.5 text-foreground">
                <span className="label">{move.label}</span>
                {move.before && <StateBadge state={move.before} />}
                <span aria-hidden>→</span>
                <StateBadge state={move.after} />
                {move.manual && <Badge>by a person</Badge>}
              </p>
              {move.reasons.length > 0 && (
                <p className="text-muted-foreground">{move.reasons.join("; ")}</p>
              )}
            </div>
          ))}
        </dd>
      </div>
      <div>
        <dt className="label">Evidence</dt>
        <dd className="mt-0.5 break-all font-mono text-[10px] text-muted-foreground">
          {Object.entries(item.evidence)
            .filter(([, ids]) => ids.length > 0)
            .map(
              ([kind, ids]) =>
                `${kind}: ${ids.slice(0, 4).join(", ")}${ids.length > 4 ? ` +${ids.length - 4}` : ""}`,
            )
            .join(" · ") || "—"}
        </dd>
      </div>
    </dl>
  );
}
