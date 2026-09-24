"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { ConditionTrace } from "@/components/condition-editor";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label, Select } from "@/components/ui/input";
import { EmptyState, ErrorState, LoadingRow } from "@/components/ui/states";
import { api } from "@/lib/api";
import { formatDate, formatRelative } from "@/lib/format";
import { cn } from "@/lib/utils";
import type {
  CurrentState,
  CustomerStateRecord,
  Page,
  Snapshot,
  SnapshotChange,
  SnapshotSummary,
  StateRefresh,
} from "@/lib/types";

/** Colours for the default machine's states. A project's own states fall back to neutral —
 *  guessing what "renewal_window" should look like would be worse than not colouring it. */
const STATE_STYLES: Record<string, string> = {
  trial: "border-info/70 bg-info/10 text-info",
  onboarding: "border-violet/70 bg-violet/10 text-violet",
  active: "border-success/70 bg-success/10 text-success",
  expanding: "border-success/70 bg-success/15 text-success",
  at_risk: "border-danger/70 bg-danger/10 text-danger",
  churned: "border-border-strong bg-surface-2 text-muted-foreground line-through",
};

export function StateBadge({
  state,
  pinned,
}: {
  state: string | null | undefined;
  pinned?: boolean;
}) {
  if (!state) return <Badge>unplaced</Badge>;
  return (
    <Badge className={STATE_STYLES[state] ?? ""} title={pinned ? "Pinned by hand" : undefined}>
      {state.replace(/_/g, " ")}
      {pinned && " ·  pinned"}
    </Badge>
  );
}

export function useCustomerState(projectId: string | null, customerId: string) {
  return useQuery({
    queryKey: ["customer-state", projectId, customerId],
    queryFn: () => api<CurrentState>(`/v1/projects/${projectId}/customers/${customerId}/state`),
    enabled: Boolean(projectId),
  });
}

/** Where the customer is in the lifecycle, how they got there, and what we knew then. */
export function StatePanel({
  projectId,
  customerId,
}: {
  projectId: string | null;
  customerId: string;
}) {
  const base = `/v1/projects/${projectId}/customers/${customerId}`;
  const queryClient = useQueryClient();
  const current = useCustomerState(projectId, customerId);
  const history = useQuery({
    queryKey: ["customer-state-history", projectId, customerId],
    queryFn: () =>
      api<Page<CustomerStateRecord>>(`${base}/state/history`, { query: { limit: 50 } }),
    enabled: Boolean(projectId),
  });
  const snapshots = useQuery({
    queryKey: ["customer-snapshots", projectId, customerId],
    queryFn: () => api<Page<SnapshotSummary>>(`${base}/snapshots`, { query: { limit: 30 } }),
    enabled: Boolean(projectId),
  });

  const invalidate = () => {
    for (const key of [
      "customer-state",
      "customer-state-history",
      "customer-snapshots",
      "customer-facts",
    ]) {
      queryClient.invalidateQueries({ queryKey: [key] });
    }
  };

  const refresh = useMutation({
    mutationFn: () => api<StateRefresh>(`${base}/state/refresh`, { method: "POST" }),
    onSuccess: invalidate,
  });
  const release = useMutation({
    mutationFn: () => api(`${base}/state/pin`, { method: "DELETE" }),
    onSuccess: invalidate,
  });

  if (current.isLoading) return <LoadingRow />;
  if (current.error) return <ErrorState error={current.error} />;
  const state = current.data;
  if (!state) return null;
  if (!state.enabled) {
    return (
      <EmptyState
        title="Lifecycle tracking is off"
        description="Turn it on in Settings → Engine → Lifecycle to place customers in states."
      />
    );
  }

  const record = state.current;
  return (
    <div className="space-y-6">
      <Card>
        <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-4">
          <div>
            <CardTitle>Lifecycle state</CardTitle>
            <p className="mt-1 text-xs text-muted-foreground">
              Decided by the project's state machine from this customer's facts. A state set by hand
              is pinned and left alone until released.
            </p>
          </div>
          <div className="flex gap-2">
            {record?.pinned && (
              <Button
                size="sm"
                variant="outline"
                loading={release.isPending}
                onClick={() => release.mutate()}
              >
                Release pin
              </Button>
            )}
            <Button
              size="sm"
              variant="secondary"
              loading={refresh.isPending}
              onClick={() => refresh.mutate()}
            >
              Re-evaluate
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          {record ? (
            <>
              <div className="flex flex-wrap items-center gap-3">
                <StateBadge state={record.state} pinned={record.pinned} />
                <span className="label">
                  since {formatRelative(record.entered_at)} · {record.source}
                  {record.transition && ` · ${record.transition.replace(/_/g, " ")}`}
                  {record.pinned_until && ` · pinned until ${formatDate(record.pinned_until)}`}
                </span>
              </div>
              {record.evaluation?.leaves?.length ? (
                <ConditionTrace evaluation={record.evaluation} />
              ) : (
                record.reason && (
                  <p className="text-[12px] text-muted-foreground">{record.reason}</p>
                )
              )}
            </>
          ) : (
            <p className="text-[12px] text-muted-foreground">
              Not placed yet — the next processed event, the nightly sweep or Re-evaluate will place
              them.
            </p>
          )}
          {refresh.data && (
            <p className="label">
              {refresh.data.moved
                ? `Moved: ${refresh.data.transitions.map((step) => `${step.from ?? "—"} → ${step.to}`).join(", ") || "placed"}`
                : "Nothing moved — no transition's condition holds."}
            </p>
          )}
          <OverrideForm
            states={state.states}
            current={record?.state ?? null}
            onSaved={invalidate}
            path={`${base}/state`}
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>History</CardTitle>
        </CardHeader>
        <CardContent>
          {history.isLoading && <LoadingRow />}
          {history.data && history.data.data.length === 0 && (
            <p className="label">No states yet.</p>
          )}
          <ol className="space-y-px">
            {history.data?.data.map((row) => (
              <li key={row.id} className="flex flex-wrap items-center gap-3 bg-surface-2 px-3 py-2">
                <StateBadge state={row.state} pinned={row.pinned && !row.exited_at} />
                <span className="label">
                  {formatDate(row.entered_at)}
                  {row.exited_at ? ` → ${formatDate(row.exited_at)}` : " → now"}
                </span>
                <span className="text-[11px] text-muted-foreground">
                  {row.source === "manual"
                    ? "set by hand"
                    : (row.transition?.replace(/_/g, " ") ?? row.source)}
                  {row.previous_state && ` · from ${row.previous_state.replace(/_/g, " ")}`}
                </span>
              </li>
            ))}
          </ol>
        </CardContent>
      </Card>

      <SnapshotsCard
        projectId={projectId}
        customerId={customerId}
        snapshots={snapshots.data?.data ?? []}
      />
    </div>
  );
}

function OverrideForm({
  states,
  current,
  path,
  onSaved,
}: {
  states: string[];
  current: string | null;
  path: string;
  onSaved: () => void;
}) {
  const [target, setTarget] = useState(current ?? states[0] ?? "");
  const [pinDays, setPinDays] = useState("");
  const [note, setNote] = useState("");
  const save = useMutation({
    mutationFn: () =>
      api(path, {
        method: "PUT",
        body: {
          state: target,
          pin: true,
          pin_days: pinDays ? Number(pinDays) : null,
          note: note || null,
        },
      }),
    onSuccess: () => {
      setNote("");
      onSaved();
    },
  });

  return (
    <div className="border-t border-border pt-4">
      <p className="label mb-2">Set by hand</p>
      <div className="flex flex-wrap items-end gap-2">
        <div>
          <Label htmlFor="state-target">State</Label>
          <Select
            id="state-target"
            value={target}
            onChange={(event) => setTarget(event.target.value)}
            className="w-40"
          >
            {states.map((state) => (
              <option key={state} value={state}>
                {state.replace(/_/g, " ")}
              </option>
            ))}
          </Select>
        </div>
        <div>
          <Label htmlFor="state-pin">Pin for (days)</Label>
          <Input
            id="state-pin"
            type="number"
            min={1}
            max={365}
            placeholder="until released"
            value={pinDays}
            onChange={(event) => setPinDays(event.target.value)}
            className="w-36"
          />
        </div>
        <div className="min-w-[14rem] flex-1">
          <Label htmlFor="state-note">Why</Label>
          <Input
            id="state-note"
            value={note}
            onChange={(event) => setNote(event.target.value)}
            placeholder="Spoke to them — they are fine."
          />
        </div>
        <Button size="sm" loading={save.isPending} onClick={() => save.mutate()}>
          Set state
        </Button>
      </div>
      {save.error && <ErrorState error={save.error} />}
    </div>
  );
}

function SnapshotsCard({
  projectId,
  customerId,
  snapshots,
}: {
  projectId: string | null;
  customerId: string;
  snapshots: SnapshotSummary[];
}) {
  const [moment, setMoment] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const base = `/v1/projects/${projectId}/customers/${customerId}`;
  const atMoment = useQuery({
    queryKey: ["snapshot-at", projectId, customerId, moment],
    queryFn: () =>
      api<Snapshot>(`${base}/snapshots/at`, { query: { time: new Date(moment).toISOString() } }),
    enabled: Boolean(projectId && moment),
    retry: false,
  });

  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-4">
        <div>
          <CardTitle>What we knew, and when</CardTitle>
          <p className="mt-1 text-xs text-muted-foreground">
            A snapshot is taken only when something material changes, so each row is a moment worth
            knowing about.
          </p>
        </div>
        <div>
          <Label htmlFor="snapshot-at">As of</Label>
          <Input
            id="snapshot-at"
            type="datetime-local"
            value={moment}
            onChange={(event) => setMoment(event.target.value)}
            className="w-56"
          />
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {moment && atMoment.isError && (
          <p className="label">Nothing was known about this customer yet at that time.</p>
        )}
        {moment && atMoment.data && (
          <FactsTable facts={atMoment.data.facts} withheld={atMoment.data.withheld_facts} />
        )}

        {snapshots.length === 0 && <p className="label">No snapshots yet.</p>}
        <ol className="space-y-px">
          {snapshots.map((snapshot) => (
            <li key={snapshot.id} className="bg-surface-2 px-3 py-2">
              <button
                className="flex w-full flex-wrap items-center gap-3 text-left"
                onClick={() => setOpen(open === snapshot.id ? null : snapshot.id)}
              >
                <span className="label w-36">{formatDate(snapshot.taken_at)}</span>
                <StateBadge state={snapshot.state} />
                {snapshot.health_score !== null && (
                  <span className="numeric text-[11px]">
                    health {Math.round(snapshot.health_score)}
                  </span>
                )}
                <span className="label">{snapshot.reason.replace(/_/g, " ")}</span>
                <span className="flex-1 truncate text-[11px] text-muted-foreground">
                  {snapshot.changes.map((change) => change.fact).join(", ") || "first snapshot"}
                </span>
              </button>
              {open === snapshot.id && <ChangeList changes={snapshot.changes} />}
            </li>
          ))}
        </ol>
      </CardContent>
    </Card>
  );
}

function ChangeList({ changes }: { changes: SnapshotChange[] }) {
  return (
    <ul className="mt-2 space-y-1 border-l-2 border-accent pl-3">
      {changes.map((change) => (
        <li key={change.fact} className="font-mono text-[11px]">
          <span className="text-muted-foreground">{change.fact}</span>{" "}
          {change.added || change.removed ? (
            <>
              {change.added?.length ? (
                <span className="text-success">+{change.added.join(", +")} </span>
              ) : null}
              {change.removed?.length ? (
                <span className="text-danger">−{change.removed.join(", −")}</span>
              ) : null}
            </>
          ) : (
            <>
              {render(change.before)} →{" "}
              <span className="text-foreground">{render(change.after)}</span>
            </>
          )}
        </li>
      ))}
    </ul>
  );
}

/** A fact document as a readable table, grouped by family. */
export function FactsTable({
  facts,
  withheld = [],
}: {
  facts: Record<string, unknown>;
  withheld?: string[];
}) {
  const groups = new Map<string, [string, unknown][]>();
  for (const [name, value] of Object.entries(facts)) {
    if (name === "customer.metadata") continue;
    const family = name.split(".")[0];
    groups.set(family, [...(groups.get(family) ?? []), [name, value]]);
  }
  return (
    <div className="grid gap-4 md:grid-cols-2">
      {[...groups.entries()].map(([family, entries]) => (
        <div key={family}>
          <p className="label mb-1">{family}</p>
          <dl className="space-y-px">
            {entries.map(([name, value]) => (
              <div key={name} className="flex gap-2 bg-surface-2 px-2 py-1">
                <dt className="w-44 shrink-0 font-mono text-[10px] text-muted-foreground">
                  {name.split(".").slice(1).join(".")}
                </dt>
                <dd
                  className={cn(
                    "min-w-0 flex-1 truncate font-mono text-[11px]",
                    withheld.includes(name) && "text-warning",
                  )}
                  title={
                    withheld.includes(name)
                      ? "Partly withheld by the restriction policy"
                      : undefined
                  }
                >
                  {render(value)}
                </dd>
              </div>
            ))}
          </dl>
        </div>
      ))}
    </div>
  );
}

function render(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (Array.isArray(value)) return value.length ? value.join(", ") : "none";
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toFixed(2);
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}
