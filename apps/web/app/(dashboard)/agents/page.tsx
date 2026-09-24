"use client";

import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { ActionsLog } from "@/components/agents/actions";
import { ApprovalsQueue } from "@/components/agents/approvals";
import { ChecksLog } from "@/components/agents/checks";
import { ProfilesManager } from "@/components/agents/profiles";
import { RunsExplorer } from "@/components/agents/runs";
import { SessionsList } from "@/components/agents/sessions";
import { PolicySimulator } from "@/components/agents/simulator";
import { StatTile } from "@/components/ui/card";
import { LoadingRow, PageHeader } from "@/components/ui/states";
import { useSession } from "@/hooks/use-session";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { AgentActivity } from "@/lib/types";

const TABS = [
  "Approvals",
  "Actions",
  "Checks",
  "Runs",
  "Sessions",
  "Profiles",
  "Simulate",
] as const;
type Tab = (typeof TABS)[number];

export default function AgentsPage() {
  // useSearchParams needs a boundary, or the page cannot be prerendered.
  return (
    <Suspense fallback={<LoadingRow />}>
      <Agents />
    </Suspense>
  );
}

function Agents() {
  const { projectId, user } = useSession();
  const params = useSearchParams();
  const [tab, setTab] = useState<Tab>("Approvals");
  const runParam = params.get("run");

  // Deep links from elsewhere: /agents?run=qry_… opens that run's explanation.
  useEffect(() => {
    const wanted = params.get("tab");
    if (runParam) setTab("Runs");
    else if (wanted && (TABS as readonly string[]).includes(wanted)) setTab(wanted as Tab);
  }, [params, runParam]);

  const activity = useQuery({
    queryKey: ["agent-activity", projectId],
    queryFn: () =>
      api<AgentActivity>(`/v1/projects/${projectId}/agent/activity`, { query: { days: 7 } }),
    enabled: Boolean(projectId),
    refetchInterval: 30_000,
  });
  const stats = activity.data;
  const pending = stats?.approvals.pending ?? 0;
  const canEdit = ["owner", "admin"].includes(user?.role ?? "");

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Agents"
        title="Agents"
        description="What your AI agents asked to do and were told, the requests waiting for a person, every answer they were given and why, and the profiles that decide what each one may read and do."
      />

      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <StatTile
          label="Waiting for a person"
          value={pending}
          tone={pending ? "accent" : "default"}
        />
        <StatTile label="Allowed · 7 days" value={stats?.checks.allow ?? 0} />
        <StatTile label="Needed approval · 7 days" value={stats?.checks.require_approval ?? 0} />
        <StatTile
          label="Denied · 7 days"
          value={stats?.checks.deny ?? 0}
          tone={stats?.checks.deny ? "danger" : "default"}
        />
        <StatTile
          label="Runs · 7 days"
          value={stats?.runs ?? 0}
          hint={stats?.top_rules[0] ? `most hit: ${stats.top_rules[0].rule}` : undefined}
        />
      </div>

      <div className="flex gap-px overflow-hidden rounded-md border border-border bg-border">
        {TABS.map((item) => (
          <button
            key={item}
            onClick={() => setTab(item)}
            className={cn(
              "flex-1 px-4 py-2.5 font-mono text-[11px] uppercase tracking-label",
              tab === item
                ? "bg-accent text-accent-foreground"
                : "bg-surface text-muted-foreground hover:bg-surface-2",
            )}
          >
            {item}
            {item === "Approvals" && pending > 0 && <span className="ml-1.5">({pending})</span>}
          </button>
        ))}
      </div>

      {tab === "Approvals" && <ApprovalsQueue projectId={projectId} />}
      {tab === "Actions" && <ActionsLog projectId={projectId} />}
      {tab === "Checks" && <ChecksLog projectId={projectId} />}
      {tab === "Runs" && <RunsExplorer projectId={projectId} initialRun={runParam} />}
      {tab === "Sessions" && <SessionsList projectId={projectId} />}
      {tab === "Profiles" && <ProfilesManager projectId={projectId} canEdit={canEdit} />}
      {tab === "Simulate" && <PolicySimulator projectId={projectId} />}
    </div>
  );
}
