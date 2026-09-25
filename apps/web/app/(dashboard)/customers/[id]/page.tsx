"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

import { ActionsLog } from "@/components/agents/actions";
import { ApprovalsQueue } from "@/components/agents/approvals";
import { ChecksLog } from "@/components/agents/checks";
import { RunsExplorer } from "@/components/agents/runs";
import { PolicySimulator } from "@/components/agents/simulator";
import { AskMemory } from "@/components/ask-memory";
import { CausalChains } from "@/components/causal-chains";
import { ChangesPanel } from "@/components/changes-panel";
import { CustomerBriefPanel } from "@/components/customer-brief";
import {
  ForecastCard,
  GoalList,
  RecommendationList,
  SignalList,
  TrajectoryBadge,
} from "@/components/foresight";
import { HealthBadge, HealthCard, HealthMeter } from "@/components/health-card";
import { MemoryGraphView } from "@/components/memory-graph";
import { Customer360View } from "@/components/customer-360";
import { FactsPanel } from "@/components/facts-panel";
import { CustomerFreshnessPanel } from "@/components/freshness";
import { StateBadge, StatePanel, useCustomerState } from "@/components/lifecycle";
import { MemoryTable } from "@/components/memory-table";
import { Timeline } from "@/components/timeline";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorState, LoadingRow } from "@/components/ui/states";
import { useSession } from "@/hooks/use-session";
import { api } from "@/lib/api";
import { formatRelative } from "@/lib/format";
import { cn } from "@/lib/utils";
import type {
  Customer,
  Customer360,
  CustomerLinks,
  Goal,
  GoalStatus,
  GraphResponse,
  Health,
  Memory,
  Page,
  Recommendations,
  SignalReport,
  TimelineEntry,
} from "@/lib/types";

const TABS = [
  "Brief",
  "Actions",
  "Changes",
  "State",
  "Facts",
  "360",
  "Memories",
  "Freshness",
  "Goals",
  "Signals",
  "Timeline",
  "Why",
  "Graph",
  "Agents",
  "Ask",
] as const;

export default function CustomerPage() {
  const params = useParams<{ id: string }>();
  const customerId = params.id;
  const { projectId } = useSession();
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<(typeof TABS)[number]>("Brief");

  const base = `/v1/projects/${projectId}/customers/${customerId}`;

  const customer = useQuery({
    queryKey: ["customer", projectId, customerId],
    queryFn: () => api<Customer>(base),
    enabled: Boolean(projectId),
  });

  const lifecycleState = useCustomerState(projectId, customerId);

  const memories = useQuery({
    queryKey: ["customer-memories", projectId, customerId],
    queryFn: () => api<Page<Memory>>(`${base}/memories`, { query: { limit: 100 } }),
    enabled: Boolean(projectId) && tab === "Memories",
  });

  const view360 = useQuery({
    queryKey: ["customer-360", projectId, customerId],
    queryFn: () => api<Customer360>(`${base}/360`),
    enabled: Boolean(projectId) && tab === "360",
  });

  const timeline = useQuery({
    queryKey: ["customer-timeline", projectId, customerId],
    queryFn: () =>
      api<{ entries: TimelineEntry[] }>(`${base}/timeline`, {
        query: { limit: 150 },
      }),
    enabled: Boolean(projectId) && tab === "Timeline",
  });

  const graph = useQuery({
    queryKey: ["customer-graph", projectId, customerId],
    queryFn: () => api<GraphResponse>(`${base}/graph`, { query: { depth: 2 } }),
    enabled: Boolean(projectId) && tab === "Graph",
  });

  const health = useQuery({
    queryKey: ["customer-health", projectId, customerId],
    queryFn: () => api<Health>(`${base}/health`),
    enabled: Boolean(projectId),
  });

  const links = useQuery({
    queryKey: ["customer-links", projectId, customerId],
    queryFn: () => api<CustomerLinks>(`${base}/links`),
    enabled: Boolean(projectId) && tab === "Why",
  });

  const signals = useQuery({
    queryKey: ["customer-signals", projectId, customerId],
    queryFn: () => api<SignalReport>(`${base}/signals`),
    enabled: Boolean(projectId),
  });

  const recommendations = useQuery({
    queryKey: ["customer-recommendations", projectId, customerId],
    queryFn: () => api<Recommendations>(`${base}/recommendations`),
    enabled: Boolean(projectId) && tab === "Actions",
  });

  const goals = useQuery({
    queryKey: ["customer-goals", projectId, customerId],
    queryFn: () => api<Page<Goal>>(`${base}/goals`, { query: { limit: 50 } }),
    enabled: Boolean(projectId) && (tab === "Goals" || tab === "Actions"),
  });

  const overrideGoal = useMutation({
    mutationFn: (input: { goal: Goal; status: GoalStatus }) =>
      api<Goal>(`/v1/projects/${projectId}/goals/${input.goal.id}`, {
        method: "PATCH",
        body: { status: input.status, note: "Set from the customer profile" },
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["customer-goals", projectId, customerId],
      });
      queryClient.invalidateQueries({
        queryKey: ["customer-signals", projectId, customerId],
      });
    },
  });

  if (customer.isLoading) return <LoadingRow />;
  if (customer.error) return <ErrorState error={customer.error} />;
  if (!customer.data) return null;

  return (
    <div className="space-y-6">
      <div className="border-b border-border pb-5">
        <Link href="/customers" className="label hover:text-accent">
          ← Customers
        </Link>
        <div className="mt-3 flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="display-xl">{customer.data.name || customer.data.external_id}</h1>
            <p className="label mt-2">
              {customer.data.external_id} · {customer.data.email ?? "no email"} · last activity{" "}
              {formatRelative(customer.data.last_event_at)}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            {(lifecycleState.data?.tracks ?? []).map((track) => (
              <button
                key={track.track}
                onClick={() => setTab("State")}
                className="flex items-center gap-3 border border-border bg-surface px-4 py-2.5 hover:border-accent"
                title={`${track.label} — ${track.current?.reasons?.join("; ") || "open the State tab"}`}
              >
                <span className="label">{track.label}</span>
                <StateBadge state={track.current?.state} pinned={track.current?.pinned} />
              </button>
            ))}
            {signals.data && (
              <div className="flex items-center gap-3 border border-border bg-surface px-4 py-2.5">
                <span className="label">Heading</span>
                <TrajectoryBadge trajectory={signals.data.trajectory} />
                <span className="numeric text-xs text-muted-foreground">
                  {Math.round(signals.data.churn_risk * 100)}% risk
                </span>
              </div>
            )}
            {health.data && (
              <div className="flex items-center gap-3 border border-border bg-surface px-4 py-2.5">
                <span className="label">Health</span>
                <HealthMeter score={health.data.score} width={14} />
                <HealthBadge band={health.data.band} />
              </div>
            )}
          </div>
        </div>
      </div>

      {health.data && <HealthCard health={health.data} />}

      {/* One row, scrolling when narrow: fifteen tabs wrapped into two uneven rows. */}
      <div className="flex gap-px overflow-x-auto rounded-md border border-border bg-border">
        {TABS.map((item) => (
          <button
            key={item}
            onClick={() => setTab(item)}
            className={cn(
              "flex-1 whitespace-nowrap px-4 py-2.5 font-mono text-[11px] uppercase tracking-label transition-colors",
              tab === item
                ? "bg-accent text-accent-foreground"
                : "bg-surface text-muted-foreground hover:bg-surface-2 hover:text-foreground",
            )}
          >
            {item}
          </button>
        ))}
      </div>

      {tab === "Brief" && (
        <CustomerBriefPanel projectId={projectId} customerId={customerId} onOpenTab={setTab} />
      )}

      {tab === "Actions" && (
        <div className="space-y-6">
          {signals.data && <ForecastCard report={signals.data} />}
          <Card>
            <CardHeader>
              <CardTitle>What to do next</CardTitle>
              <p className="mt-1 text-xs text-muted-foreground">
                {recommendations.data?.summary ?? "Derived from this customer's own history."}
              </p>
            </CardHeader>
            {recommendations.isLoading && <LoadingRow />}
            {recommendations.error && <ErrorState error={recommendations.error} />}
            {recommendations.data && (
              <RecommendationList
                recommendations={recommendations.data.recommendations}
                customerId={customerId}
              />
            )}
          </Card>
        </div>
      )}

      {tab === "Changes" && <ChangesPanel projectId={projectId} customerId={customerId} />}

      {tab === "State" && <StatePanel projectId={projectId} customerId={customerId} />}

      {tab === "Facts" && <FactsPanel projectId={projectId} customerId={customerId} />}

      {tab === "360" && (
        <>
          {view360.isLoading && <LoadingRow />}
          {view360.error && <ErrorState error={view360.error} />}
          {view360.data && <Customer360View view={view360.data} />}
        </>
      )}

      {tab === "Signals" && (
        <Card>
          <CardHeader>
            <CardTitle>What points which way</CardTitle>
            <p className="mt-1 text-xs text-muted-foreground">
              {signals.data?.headline ?? "Measured from two windows of this customer's memory."}
            </p>
          </CardHeader>
          {signals.isLoading && <LoadingRow />}
          {signals.error && <ErrorState error={signals.error} />}
          {signals.data && <SignalList signals={signals.data.signals} />}
        </Card>
      )}

      {tab === "Goals" && (
        <Card>
          <CardHeader>
            <CardTitle>What they are trying to do</CardTitle>
            <p className="mt-1 text-xs text-muted-foreground">
              Opened when they say it, closed when something in their history says it happened.
              Setting a status by hand stops the tracker touching that goal.
            </p>
          </CardHeader>
          {goals.isLoading && <LoadingRow />}
          {goals.error && <ErrorState error={goals.error} />}
          {overrideGoal.error && <ErrorState error={overrideGoal.error} />}
          {goals.data && (
            <GoalList
              goals={goals.data.data}
              pendingId={overrideGoal.isPending ? overrideGoal.variables?.goal.id : null}
              onOverride={(goal, status) => overrideGoal.mutate({ goal, status })}
            />
          )}
        </Card>
      )}

      {tab === "Memories" && (
        <Card>
          {memories.isLoading && <LoadingRow />}
          {memories.error && <ErrorState error={memories.error} />}
          {memories.data && (
            <MemoryTable memories={memories.data.data} withheld={memories.data.withheld ?? 0} />
          )}
        </Card>
      )}

      {tab === "Freshness" && (
        <CustomerFreshnessPanel projectId={projectId} customerId={customerId} />
      )}

      {tab === "Timeline" && (
        <Card>
          <CardHeader>
            <CardTitle>What happened, and what we concluded</CardTitle>
          </CardHeader>
          <CardContent>
            {timeline.isLoading && <LoadingRow />}
            {timeline.error && <ErrorState error={timeline.error} />}
            {timeline.data && <Timeline entries={timeline.data.entries} />}
          </CardContent>
        </Card>
      )}

      {tab === "Why" && (
        <div>
          {links.isLoading && <LoadingRow />}
          {links.error && <ErrorState error={links.error} />}
          {links.data && <CausalChains data={links.data} />}
        </div>
      )}

      {tab === "Graph" && (
        <Card className="overflow-hidden">
          {graph.isLoading && <LoadingRow />}
          {graph.error && <ErrorState error={graph.error} />}
          {graph.data && <MemoryGraphView graph={graph.data} />}
        </Card>
      )}

      {tab === "Agents" && (
        <div className="space-y-6">
          <section className="space-y-3">
            <p className="label-strong">Before an agent acts on this customer</p>
            <PolicySimulator projectId={projectId} customerId={customerId} />
          </section>
          <section className="space-y-3">
            <p className="label-strong">Requests waiting for a person</p>
            <ApprovalsQueue projectId={projectId} customerId={customerId} />
          </section>
          <section className="space-y-3">
            <p className="label-strong">What agents did</p>
            <ActionsLog projectId={projectId} customerId={customerId} />
          </section>
          <section className="space-y-3">
            <p className="label-strong">What agents asked to do</p>
            <ChecksLog projectId={projectId} customerId={customerId} />
          </section>
          <section className="space-y-3">
            <p className="label-strong">What agents were told</p>
            <RunsExplorer projectId={projectId} customerId={customerId} />
          </section>
        </div>
      )}

      {tab === "Ask" && <AskMemory customerId={customerId} />}
    </div>
  );
}
