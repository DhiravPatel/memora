"use client";

import { useQuery } from "@tanstack/react-query";

import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { EmptyState, ErrorState, LoadingRow, PageHeader } from "@/components/ui/states";
import { Table, TD, TH, THead, TR } from "@/components/ui/table";
import { useSession } from "@/hooks/use-session";
import { api } from "@/lib/api";
import { formatRelative } from "@/lib/format";
import type { Entity, Page } from "@/lib/types";

export default function EntitiesPage() {
  const { projectId } = useSession();

  const entities = useQuery({
    queryKey: ["entities", projectId],
    queryFn: () =>
      api<Page<Entity>>(`/v1/projects/${projectId}/entities`, { query: { limit: 200 } }),
    enabled: Boolean(projectId),
  });

  const maxMentions = Math.max(1, ...(entities.data?.data ?? []).map((item) => item.mention_count));

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Memory"
        title="Entities"
        description="Everything customers interact with: integrations, features, plans, companies and people."
      />

      <Card>
        {entities.isLoading && <LoadingRow />}
        {entities.error && <ErrorState error={entities.error} />}
        {entities.data?.data.length === 0 && (
          <EmptyState
            title="No entities yet"
            description="Entities are extracted from events as customers mention integrations, features and plans."
          />
        )}
        {!!entities.data?.data.length && (
          <Table>
            <THead>
              <TR>
                <TH>Name</TH>
                <TH className="w-40">Type</TH>
                <TH className="w-56">Mentions</TH>
                <TH className="w-32">First seen</TH>
              </TR>
            </THead>
            <tbody>
              {entities.data.data.map((entity) => (
                <TR key={entity.id}>
                  <TD className="text-sm font-semibold">{entity.name}</TD>
                  <TD>
                    <Badge>{entity.type.replace(/_/g, " ")}</Badge>
                  </TD>
                  <TD>
                    <div className="flex items-center gap-2">
                      <div className="h-[6px] w-32 overflow-hidden rounded-full bg-surface-3">
                        <div
                          className="h-full bg-accent"
                          style={{ width: `${(entity.mention_count / maxMentions) * 100}%` }}
                        />
                      </div>
                      <span className="numeric text-[11px] text-muted-foreground">
                        {entity.mention_count}
                      </span>
                    </div>
                  </TD>
                  <TD className="label">{formatRelative(entity.created_at)}</TD>
                </TR>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </div>
  );
}
