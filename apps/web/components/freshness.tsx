"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { Badge, MemoryTypeBadge, ScoreBar } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Select } from "@/components/ui/input";
import { EmptyState, ErrorState, LoadingRow } from "@/components/ui/states";
import { api } from "@/lib/api";
import { formatDate, formatRelative } from "@/lib/format";
import { cn } from "@/lib/utils";
import type {
  CustomerFreshness,
  DriftFlag,
  DriftRun,
  Freshness,
  FreshnessState,
  Page,
} from "@/lib/types";

const STATES: FreshnessState[] = ["active", "aging", "stale", "outdated", "conflicted"];

export const FRESHNESS_STYLES: Record<string, string> = {
  active: "border-success/70 bg-success/10 text-success",
  aging: "border-border bg-surface-2 text-muted-foreground",
  stale: "border-warning/70 bg-warning/10 text-warning",
  outdated: "border-danger/70 bg-danger/10 text-danger",
  conflicted: "border-violet/70 bg-violet/10 text-violet",
  expired: "border-border bg-surface-2 text-muted-foreground line-through",
  superseded: "border-border bg-surface-2 text-muted-foreground line-through",
};

const STATE_WORDS: Record<string, string> = {
  outdated: "possibly outdated",
};

/** How current a memory is (§26 5.5). Nothing for a fresh one: only what needs a look. */
export function FreshnessBadge({
  freshness,
  state,
}: {
  freshness?: Freshness | null;
  state?: FreshnessState | string | null;
}) {
  const current = freshness?.state ?? state;
  if (!current || current === "active") return null;
  return (
    <Badge
      className={FRESHNESS_STYLES[current] ?? ""}
      title={freshness?.reasons?.join(" ") || undefined}
    >
      {STATE_WORDS[current] ?? current}
    </Badge>
  );
}

function useDriftActions(projectId: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: { flag: DriftFlag; verdict: DriftVerdict; note: string }) =>
      api<DriftFlag>(`/v1/projects/${projectId}/drift/${input.flag.id}/${input.verdict}`, {
        method: "POST",
        body: { note: input.note || null },
      }),
    onSuccess: () => {
      for (const key of [
        "drift",
        "customer-freshness",
        "customer-brief",
        "customer-memories",
        "customer-state",
        "quality",
      ]) {
        queryClient.invalidateQueries({ queryKey: [key] });
      }
    },
  });
}

type DriftVerdict = "confirm" | "keep" | "dismiss";

function titled(value: string | null | undefined): string {
  if (!value) return "";
  return value.charAt(0).toUpperCase() + value.slice(1);
}

/** The three decisions, in the words of the flag: "Confirm WhatsApp", "Keep email", "Dismiss". */
function driftChoices(
  flag: DriftFlag,
): { verdict: DriftVerdict; label: string; title: string }[] {
  // Channels arrive as people write them ("WhatsApp", "email"); plans arrive lowercase.
  const [confirm, keep] =
    flag.kind === "channel"
      ? [`Confirm ${flag.observed || "the change"}`, `Keep ${flag.stated}`]
      : flag.kind === "plan"
        ? [`Confirm ${titled(flag.observed) || "the change"}`, `Keep ${titled(flag.stated)}`]
        : flag.kind === "usage"
          ? ["They stopped using it", "They still use it"]
          : ["It is resolved", "Still a problem"];
  return [
    {
      verdict: "confirm",
      label: confirm,
      title: "The evidence is right: write the change; the old memory is kept as history",
    },
    {
      verdict: "keep",
      label: keep,
      title: "The memory is right: confirm it (more confidence, new evidence); counting restarts",
    },
    {
      verdict: "dismiss",
      label: "Dismiss",
      title:
        "Not on this evidence: leave the memory as it is; only newer evidence raises this again",
    },
  ];
}

/** One flag: what the memory says, what the evidence says, and the three things a person can do. */
function DriftRow({
  flag,
  projectId,
  showCustomer,
}: {
  flag: DriftFlag;
  projectId: string | null;
  showCustomer?: boolean;
}) {
  const [note, setNote] = useState("");
  const act = useDriftActions(projectId);
  const open = flag.status === "open";
  return (
    <li
      className={cn(
        "space-y-2 border-t border-l-2 border-t-border px-5 py-3",
        open ? "border-l-danger" : "border-l-border",
      )}
    >
      <div className="flex flex-wrap items-center gap-1.5">
        <Badge className="border-danger/70 bg-danger/10 text-danger">{flag.kind_label}</Badge>
        {!open && <Badge>{flag.status}</Badge>}
        {showCustomer && (
          <Link
            href={`/customers/${flag.customer.external_id ?? flag.customer.id}`}
            className="label hover:text-accent"
          >
            {flag.customer.name || flag.customer.external_id || flag.customer.id}
          </Link>
        )}
        <span className="label" title={formatDate(flag.detected_at)}>
          flagged {formatRelative(flag.detected_at)}
        </span>
      </div>
      {flag.memory.content && (
        <p className="text-sm leading-snug text-foreground">
          <span className="text-muted-foreground">Memory: </span>
          {flag.memory.content}
        </p>
      )}
      <p className="text-sm leading-snug text-foreground">{flag.summary}</p>
      {flag.evidence.length > 0 && (
        <p className="font-mono text-[10px] text-muted-foreground" title={flag.evidence.join(", ")}>
          evidence: {flag.evidence.slice(0, 3).join(", ")}
          {flag.evidence.length > 3 ? ` +${flag.evidence.length - 3}` : ""}
        </p>
      )}
      {open ? (
        <div className="flex flex-wrap items-center gap-2">
          <Input
            value={note}
            onChange={(event) => setNote(event.target.value)}
            placeholder="Note (optional)"
            className="h-7 w-56 text-[11px]"
          />
          {driftChoices(flag).map((choice) => (
            <Button
              key={choice.verdict}
              size="sm"
              variant={
                choice.verdict === "confirm"
                  ? "primary"
                  : choice.verdict === "keep"
                    ? "outline"
                    : "ghost"
              }
              disabled={act.isPending && act.variables?.verdict !== choice.verdict}
              loading={act.isPending && act.variables?.verdict === choice.verdict}
              onClick={() => act.mutate({ flag, verdict: choice.verdict, note })}
              title={choice.title}
            >
              {choice.label}
            </Button>
          ))}
        </div>
      ) : (
        <p className="text-xs text-muted-foreground">
          {flag.status} {flag.resolved_at ? formatRelative(flag.resolved_at) : ""}
          {flag.resolved_by_type === "system" ? " by the system" : ""}
          {flag.note ? ` — ${flag.note}` : ""}
          {flag.replacement_memory_id ? ` · wrote ${flag.replacement_memory_id}` : ""}
        </p>
      )}
      {act.error && <ErrorState error={act.error} />}
    </li>
  );
}

/** Drift flags to review — one customer's, or the whole project's. */
export function DriftQueue({
  projectId,
  customerId,
  title = "Possibly out of date",
}: {
  projectId: string | null;
  customerId?: string;
  title?: string;
}) {
  const [status, setStatus] = useState("open");
  const [kind, setKind] = useState("");
  const flags = useQuery({
    queryKey: ["drift", projectId, customerId ?? null, status, kind],
    queryFn: () =>
      api<Page<DriftFlag>>(`/v1/projects/${projectId}/drift`, {
        query: { status, kind: kind || undefined, customer_id: customerId, limit: 100 },
      }),
    enabled: Boolean(projectId),
  });
  const rows = flags.data?.data ?? [];
  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <CardTitle>{title}</CardTitle>
            <p className="mt-1 text-xs text-muted-foreground">
              Evidence since a memory was stated points the other way. Nothing is changed until a
              person decides.
            </p>
          </div>
          <div className="flex gap-2">
            <Select
              value={status}
              onChange={(event) => setStatus(event.target.value)}
              className="w-32"
            >
              {["open", "confirmed", "kept", "dismissed", "cleared", "all"].map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </Select>
            <Select value={kind} onChange={(event) => setKind(event.target.value)} className="w-40">
              <option value="">every kind</option>
              <option value="channel">contact channel</option>
              <option value="plan">plan</option>
              <option value="usage">feature use</option>
              <option value="quiet_problem">quiet problem</option>
            </Select>
          </div>
        </div>
      </CardHeader>
      {flags.isLoading && <LoadingRow />}
      {flags.error && <ErrorState error={flags.error} />}
      {flags.data && rows.length === 0 && (
        <EmptyState
          title={status === "open" ? "Nothing looks out of date" : "Nothing here"}
          description="Channels customers actually use, plans billing names, features they stopped using and problems that went quiet are checked on every event and nightly."
        />
      )}
      {rows.length > 0 && (
        <ul>
          {rows.map((flag) => (
            <DriftRow key={flag.id} flag={flag} projectId={projectId} showCustomer={!customerId} />
          ))}
        </ul>
      )}
      {(flags.data?.withheld ?? 0) > 0 && (
        <p className="border-t border-border px-5 py-2 text-[11px] text-muted-foreground">
          {flags.data?.withheld} flag{flags.data?.withheld === 1 ? " is" : "s are"} about memories
          you may not read.
        </p>
      )}
    </Card>
  );
}

function StateStrip({ counts }: { counts: Partial<Record<string, number>> }) {
  const total = STATES.reduce((sum, state) => sum + (counts[state] ?? 0), 0);
  return (
    <div className="space-y-2">
      <div className="flex h-2 overflow-hidden rounded-sm border border-border">
        {total > 0 &&
          STATES.map((state) =>
            counts[state] ? (
              <span
                key={state}
                className={cn(
                  "h-full",
                  FRESHNESS_STYLES[state].split(" ").find((token) => token.startsWith("bg-")),
                )}
                style={{ width: `${((counts[state] ?? 0) / total) * 100}%` }}
                title={`${counts[state]} ${state}`}
              />
            ) : null,
          )}
      </div>
      <div className="flex flex-wrap gap-3">
        {STATES.map((state) => (
          <span key={state} className="label">
            <span className="numeric text-foreground">{counts[state] ?? 0}</span>{" "}
            {STATE_WORDS[state] ?? state}
          </span>
        ))}
      </div>
    </div>
  );
}

/** One customer: how current each memory is, the ones to look at first, and the flags. */
export function CustomerFreshnessPanel({
  projectId,
  customerId,
}: {
  projectId: string | null;
  customerId: string;
}) {
  const queryClient = useQueryClient();
  const report = useQuery({
    queryKey: ["customer-freshness", projectId, customerId],
    queryFn: () =>
      api<CustomerFreshness>(`/v1/projects/${projectId}/customers/${customerId}/freshness`),
    enabled: Boolean(projectId),
  });
  const refresh = useMutation({
    mutationFn: () =>
      api<DriftRun>(`/v1/projects/${projectId}/customers/${customerId}/drift/refresh`, {
        method: "POST",
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["customer-freshness", projectId, customerId] });
      queryClient.invalidateQueries({ queryKey: ["drift"] });
    },
  });
  const body = report.data;
  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <CardTitle>How current this is</CardTitle>
              <p className="mt-1 text-xs text-muted-foreground">
                Each memory against its type&apos;s window: problems go stale in weeks, facts in a
                year. A person confirming a memory counts as new evidence.
              </p>
            </div>
            <Button
              size="sm"
              variant="secondary"
              loading={refresh.isPending}
              onClick={() => refresh.mutate()}
            >
              Check now
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          {report.isLoading && <LoadingRow />}
          {report.error && <ErrorState error={report.error} />}
          {refresh.data && (
            <p className="text-xs text-muted-foreground">
              Checked: {refresh.data.opened.length} new, {refresh.data.refreshed.length} still open,{" "}
              {refresh.data.cleared.length} cleared.
            </p>
          )}
          {body && <StateStrip counts={body.counts} />}
          {body && body.withheld > 0 && (
            <p className="text-[11px] text-muted-foreground">
              {body.withheld} memor{body.withheld === 1 ? "y is" : "ies are"} not shown to you.
            </p>
          )}
        </CardContent>
        {body && body.memories.length > 0 && (
          <ul>
            {body.memories.map((item) => (
              <li key={item.id} className="flex gap-4 border-t border-border px-5 py-3">
                <div className="min-w-0 flex-1 space-y-1">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <MemoryTypeBadge type={item.type} />
                    <FreshnessBadge freshness={item.freshness} />
                  </div>
                  <p className="text-sm leading-snug text-foreground">{item.content}</p>
                  {item.freshness.reasons.map((reason) => (
                    <p key={reason} className="text-xs text-muted-foreground">
                      {reason}
                    </p>
                  ))}
                </div>
                <div className="w-32 shrink-0 space-y-1">
                  <span className="label">effective confidence</span>
                  <ScoreBar value={item.freshness.effective_confidence} />
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>
      <DriftQueue projectId={projectId} customerId={customerId} />
    </div>
  );
}

/** The project: freshness by state and drift by kind, then the review queue. */
export function FreshnessOverview({
  projectId,
  memories,
}: {
  projectId: string | null;
  memories?: Record<string, any> | null;
}) {
  const freshness = (memories?.freshness ?? {}) as Record<string, number>;
  const byKind = (memories?.drift_open_by_kind ?? {}) as Record<string, number>;
  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Freshness</CardTitle>
          <p className="mt-1 text-xs text-muted-foreground">
            Standing memories by how current they are. Windows per type are in{" "}
            <Link href="/settings" className="underline hover:text-accent">
              Settings → Freshness and drift
            </Link>
            .
          </p>
        </CardHeader>
        <CardContent className="space-y-3">
          <StateStrip counts={freshness} />
          <div className="flex flex-wrap gap-3">
            {Object.entries(byKind).map(([kind, count]) => (
              <span key={kind} className="label">
                <span className="numeric text-foreground">{count}</span> open{" "}
                {kind.replace(/_/g, " ")}
              </span>
            ))}
          </div>
        </CardContent>
      </Card>
      <DriftQueue projectId={projectId} title="Review drift" />
    </div>
  );
}
