"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";

import { UsageChart } from "@/components/usage-chart";
import { MemoryTypeBadge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle, StatTile } from "@/components/ui/card";
import { EmptyState, ErrorState, LoadingRow, PageHeader } from "@/components/ui/states";
import { useSession } from "@/hooks/use-session";
import { api } from "@/lib/api";
import { formatNumber } from "@/lib/format";
import type { Overview, UsageResponse } from "@/lib/types";

export default function OverviewPage() {
  const { projectId, project } = useSession();

  const overview = useQuery({
    queryKey: ["overview", projectId],
    queryFn: () => api<Overview>(`/v1/projects/${projectId}/overview`),
    enabled: Boolean(projectId),
  });

  const usage = useQuery({
    queryKey: ["usage", projectId],
    queryFn: () => api<UsageResponse>(`/v1/projects/${projectId}/usage`, { query: { days: 30 } }),
    enabled: Boolean(projectId),
  });

  if (!projectId) {
    return (
      <Card>
        <EmptyState
          title="No project yet"
          description="Create a project to start sending events."
          action={
            <Link href="/settings" className="label-strong mt-2 text-accent hover:underline">
              Go to settings →
            </Link>
          }
        />
      </Card>
    );
  }

  const stats = overview.data;
  const totalMemories = stats
    ? Object.values(stats.memories_by_type).reduce((sum, value) => sum + value, 0)
    : 0;

  return (
    <div className="space-y-7">
      <PageHeader
        eyebrow={`Project · ${project?.name ?? "—"}`}
        title="Overview"
        description="Everything this project knows about its customers, derived deterministically from raw events."
      />

      {overview.isLoading && <LoadingRow />}
      {overview.error && <ErrorState error={overview.error} />}

      {stats && (
        <>
          <div className="grid gap-px border border-border bg-border sm:grid-cols-2 lg:grid-cols-4">
            <StatTile label="Customers" value={formatNumber(stats.total_customers)} tone="accent" />
            <StatTile label="Events" value={formatNumber(stats.total_events)} />
            <StatTile label="Memories" value={formatNumber(stats.total_memories)} tone="accent" />
            <StatTile label="Queries · 30d" value={formatNumber(stats.ai_queries)} />
            <StatTile
              label="Processed"
              value={formatNumber(stats.events_processed)}
              hint={`${stats.events_pending} pending`}
            />
            <StatTile
              label="Failed"
              value={formatNumber(stats.events_failed)}
              tone={stats.events_failed ? "danger" : "default"}
            />
            <StatTile label="Entities" value={formatNumber(stats.total_entities)} />
            <StatTile label="Graph edges" value={formatNumber(stats.total_relationships)} />
          </div>

          <div className="grid gap-5 lg:grid-cols-3">
            <Card className="lg:col-span-2">
              <CardHeader className="flex items-center justify-between">
                <CardTitle>Usage · last 30 days</CardTitle>
                <span className="label">events / memories / queries</span>
              </CardHeader>
              <CardContent className="pt-5">
                {usage.isLoading ? <LoadingRow /> : <UsageChart series={usage.data?.series ?? []} />}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Memories by type</CardTitle>
              </CardHeader>
              <CardContent className="space-y-2.5">
                {Object.keys(stats.memories_by_type).length === 0 && (
                  <p className="text-xs text-muted-foreground">
                    No memories yet. Send an event to get started.
                  </p>
                )}
                {Object.entries(stats.memories_by_type)
                  .sort((a, b) => b[1] - a[1])
                  .map(([type, count]) => (
                    <div key={type} className="flex items-center gap-3">
                      <MemoryTypeBadge type={type} />
                      <div className="h-[6px] flex-1 overflow-hidden rounded-full bg-surface-3">
                        <div
                          className="h-full bg-accent"
                          style={{
                            width: `${totalMemories ? (count / totalMemories) * 100 : 0}%`,
                          }}
                        />
                      </div>
                      <span className="numeric w-6 text-right text-[11px] text-muted-foreground">
                        {count}
                      </span>
                    </div>
                  ))}
              </CardContent>
            </Card>
          </div>
        </>
      )}
    </div>
  );
}
