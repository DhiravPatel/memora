"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { ContractEditor, MODE_STYLES } from "@/components/contracts/contract-editor";
import { ContractReport } from "@/components/contracts/contract-report";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Select } from "@/components/ui/input";
import { EmptyState, ErrorState, LoadingRow, PageHeader } from "@/components/ui/states";
import { Table, TD, TH, THead, TR } from "@/components/ui/table";
import { useSession } from "@/hooks/use-session";
import { api } from "@/lib/api";
import { contractYaml } from "@/lib/contract-yaml";
import { formatNumber, formatRelative } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { ContractCoverage, EventContract } from "@/lib/types";

export default function ContractsPage() {
  return (
    <Suspense fallback={<LoadingRow />}>
      <Contracts />
    </Suspense>
  );
}

function Contracts() {
  const { projectId } = useSession();
  const queryClient = useQueryClient();
  const router = useRouter();
  // `?type=payment_failed` arrives from an event that broke its contract.
  const params = useSearchParams();
  const [selected, setSelected] = useState<string | null>(params.get("type"));
  const [since, setSince] = useState("7d");
  const [editing, setEditing] = useState(false);
  const [creating, setCreating] = useState(false);
  const [showYaml, setShowYaml] = useState(false);

  const coverage = useQuery({
    queryKey: ["contract-coverage", projectId, since],
    queryFn: () =>
      api<ContractCoverage[]>(`/v1/projects/${projectId}/contracts/coverage`, { query: { since } }),
    enabled: Boolean(projectId),
  });
  const row = coverage.data?.find((item) => item.event_type === selected) ?? null;
  const covered = Boolean(row?.mode);

  const contract = useQuery({
    queryKey: ["contract", projectId, selected, since],
    queryFn: () =>
      api<EventContract>(`/v1/projects/${projectId}/contracts/${encodeURIComponent(selected!)}`, {
        query: { since },
      }),
    enabled: Boolean(projectId && selected && covered),
  });

  const remove = useMutation({
    mutationFn: (eventType: string) =>
      api(`/v1/projects/${projectId}/contracts/${encodeURIComponent(eventType)}`, {
        method: "DELETE",
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["contract-coverage"] });
      void queryClient.invalidateQueries({ queryKey: ["contract"] });
      setEditing(false);
    },
  });

  function pick(eventType: string | null) {
    setSelected(eventType);
    setEditing(false);
    setCreating(false);
    setShowYaml(false);
    router.replace(eventType ? `/contracts?type=${encodeURIComponent(eventType)}` : "/contracts");
  }

  function saved(result: EventContract) {
    void queryClient.invalidateQueries({ queryKey: ["contract-coverage"] });
    queryClient.setQueryData(["contract", projectId, result.event_type, since], undefined);
    void queryClient.invalidateQueries({ queryKey: ["contract", projectId, result.event_type] });
    setCreating(false);
    setEditing(false);
    setSelected(result.event_type);
    router.replace(`/contracts?type=${encodeURIComponent(result.event_type)}`);
  }

  const items = coverage.data ?? [];
  const contracted = items.filter((item) => item.mode).length;
  const uncovered = items.filter((item) => !item.mode && item.events > 0);

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Pipeline"
        title="Memory contracts"
        description="What each event type must look like — required fields, types, the field that carries the text, how important it is. Checked as events arrive: warn keeps an event that breaks its contract and reports how; enforce refuses it."
        actions={
          <>
            <Select
              aria-label="Window"
              value={since}
              onChange={(event) => setSince(event.target.value)}
              className="w-32"
            >
              <option value="24h">24 hours</option>
              <option value="7d">7 days</option>
              <option value="30d">30 days</option>
            </Select>
            <Button
              size="sm"
              onClick={() => {
                pick(null);
                setCreating(true);
              }}
            >
              New contract
            </Button>
          </>
        }
      />

      {creating && projectId && (
        <Card>
          <CardHeader>
            <CardTitle>New contract</CardTitle>
            <CardDescription>
              Name the event type, then write its fields — or generate them from the type&apos;s
              recent events and adjust.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <ContractEditor
              projectId={projectId}
              onSaved={saved}
              onCancel={() => setCreating(false)}
            />
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Event types</CardTitle>
          <CardDescription>
            {items.length
              ? `${contracted} of ${items.length} event type${items.length === 1 ? "" : "s"} have a contract${
                  uncovered.length ? ` · ${uncovered.length} received without one` : ""
                }.`
              : "Every event type received in the window, and the contract covering it."}
          </CardDescription>
        </CardHeader>
        {coverage.isLoading && <LoadingRow />}
        {coverage.error && <ErrorState error={coverage.error} />}
        {coverage.data && items.length === 0 && (
          <EmptyState
            title="No events in this window"
            description="Event types appear here as they are received. A contract can be written before the first one arrives."
          />
        )}
        {items.length > 0 && (
          <Table className="min-w-[760px]">
            <THead>
              <TR>
                <TH>Event type</TH>
                <TH className="w-28 text-right">Events</TH>
                <TH className="w-40 text-right">Breaking contract</TH>
                <TH className="w-32">Last seen</TH>
                <TH className="w-44">Contract</TH>
              </TR>
            </THead>
            <tbody>
              {items.map((item) => (
                <TR
                  key={item.event_type}
                  className={cn("cursor-pointer", selected === item.event_type && "bg-surface-2")}
                  onClick={() => pick(item.event_type)}
                >
                  <TD className="font-mono text-[11px] uppercase tracking-label text-foreground">
                    {item.event_type}
                  </TD>
                  <TD className="numeric text-right text-xs">{formatNumber(item.events)}</TD>
                  <TD
                    className={cn(
                      "numeric text-right text-xs",
                      item.violating ? "font-semibold text-danger" : "text-muted-foreground",
                    )}
                  >
                    {item.mode ? formatNumber(item.violating) : "—"}
                  </TD>
                  <TD className="label whitespace-nowrap">{formatRelative(item.last_seen_at)}</TD>
                  <TD>
                    {item.mode ? (
                      <span className="flex items-center gap-2">
                        <Badge className={MODE_STYLES[item.mode]}>{item.mode}</Badge>
                        <span className="label">v{item.version}</span>
                      </span>
                    ) : (
                      <span className="label text-faint">none · write one →</span>
                    )}
                  </TD>
                </TR>
              ))}
            </tbody>
          </Table>
        )}
      </Card>

      {selected && projectId && !creating && (
        <Card>
          <CardHeader className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <CardTitle className="flex items-center gap-2">
                {selected}
                {contract.data && (
                  <>
                    <Badge className={MODE_STYLES[contract.data.mode]}>{contract.data.mode}</Badge>
                    <span className="label">version {contract.data.version}</span>
                  </>
                )}
              </CardTitle>
              <CardDescription>
                {contract.data
                  ? contract.data.definition.description ||
                    `Updated ${formatRelative(contract.data.updated_at)}.`
                  : covered
                    ? "Loading…"
                    : "No contract covers this event type yet. Generate one from its recent events, or write it."}
              </CardDescription>
            </div>
            {contract.data && (
              <div className="flex flex-wrap gap-2">
                <Button size="sm" variant="outline" onClick={() => setShowYaml((value) => !value)}>
                  {showYaml ? "Hide YAML" : "YAML"}
                </Button>
                <Button
                  size="sm"
                  variant={editing ? "primary" : "outline"}
                  onClick={() => setEditing((value) => !value)}
                >
                  {editing ? "Editing" : "Edit"}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  loading={remove.isPending}
                  onClick={() => {
                    if (
                      window.confirm(
                        `Delete the ${selected} contract? Its events will no longer be checked.`,
                      )
                    ) {
                      remove.mutate(selected);
                    }
                  }}
                >
                  Delete
                </Button>
              </div>
            )}
          </CardHeader>
          <CardContent className="space-y-5">
            {contract.error && <ErrorState error={contract.error} />}
            {remove.error && <ErrorState error={remove.error} />}
            {showYaml && contract.data && (
              <pre className="overflow-auto border border-border bg-surface-2 p-3 font-mono text-[11px] leading-relaxed">
                {contractYaml(contract.data.definition)}
              </pre>
            )}
            {(editing || !covered) && (
              <ContractEditor
                key={`${selected}-${contract.data?.version ?? "new"}`}
                projectId={projectId}
                eventType={selected}
                existing={contract.data ?? null}
                onSaved={saved}
                onCancel={covered ? () => setEditing(false) : undefined}
              />
            )}
            {contract.data && !editing && <ContractReport contract={contract.data} since={since} />}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
