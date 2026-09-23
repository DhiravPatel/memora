"use client";

import { useQuery } from "@tanstack/react-query";

import { Badge } from "@/components/ui/badge";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState, ErrorState, LoadingRow, PageHeader } from "@/components/ui/states";
import { Table, TD, TH, THead, TR } from "@/components/ui/table";
import { useSession } from "@/hooks/use-session";
import { api } from "@/lib/api";
import { formatDate } from "@/lib/format";
import type { AuditLog, QueryLog } from "@/lib/types";

export default function AuditPage() {
  const { projectId } = useSession();

  const logs = useQuery({
    queryKey: ["audit", projectId],
    queryFn: () => api<AuditLog[]>(`/v1/projects/${projectId}/audit-logs`, { query: { limit: 100 } }),
    enabled: Boolean(projectId),
  });

  const queries = useQuery({
    queryKey: ["query-logs", projectId],
    queryFn: () => api<QueryLog[]>(`/v1/projects/${projectId}/queries`, { query: { limit: 50 } }),
    enabled: Boolean(projectId),
  });

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="System"
        title="Audit"
        description="Authentication, configuration changes, memory changes and deletions — plus every answer the engine produced."
      />

      <Card>
        <CardHeader>
          <CardTitle>Audit log</CardTitle>
        </CardHeader>
        {logs.isLoading && <LoadingRow />}
        {logs.error && <ErrorState error={logs.error} />}
        {logs.data?.length === 0 && <EmptyState title="No audit entries yet" />}
        {!!logs.data?.length && (
          <Table>
            <THead>
              <TR>
                <TH className="w-52">Action</TH>
                <TH className="w-52">Actor</TH>
                <TH>Resource</TH>
                <TH className="w-36">When</TH>
              </TR>
            </THead>
            <tbody>
              {logs.data.map((entry) => (
                <TR key={entry.id}>
                  <TD>
                    <Badge>{entry.action.replace(/_/g, " ")}</Badge>
                  </TD>
                  <TD className="label">
                    {entry.actor_type}
                    {entry.actor_id ? ` · ${entry.actor_id}` : ""}
                  </TD>
                  <TD className="label">
                    {entry.resource_type ?? "—"}
                    {entry.resource_id ? ` · ${entry.resource_id}` : ""}
                  </TD>
                  <TD className="label">{formatDate(entry.created_at)}</TD>
                </TR>
              ))}
            </tbody>
          </Table>
        )}
      </Card>

      <Card>
        <CardHeader className="flex items-center justify-between">
          <CardTitle>Answers produced</CardTitle>
          <span className="label">query → memories → source events</span>
        </CardHeader>
        {queries.isLoading && <LoadingRow />}
        {queries.data?.length === 0 && <EmptyState title="No queries yet" />}
        {!!queries.data?.length && (
          <Table>
            <THead>
              <TR>
                <TH className="w-72">Question</TH>
                <TH>Answer</TH>
                <TH className="w-24">Memories</TH>
                <TH className="w-24">Latency</TH>
              </TR>
            </THead>
            <tbody>
              {queries.data.map((entry) => (
                <TR key={entry.id}>
                  <TD className="text-xs">{entry.query}</TD>
                  <TD className="max-w-xl text-xs leading-relaxed text-muted-foreground">
                    {entry.answer ?? "—"}
                  </TD>
                  <TD className="numeric text-[11px] text-muted-foreground">
                    {entry.memory_ids.length}
                  </TD>
                  <TD className="numeric text-[11px] text-muted-foreground">
                    {entry.latency_ms} ms
                  </TD>
                </TR>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </div>
  );
}
