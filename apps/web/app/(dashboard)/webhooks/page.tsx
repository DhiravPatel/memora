"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Badge, StatusBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label } from "@/components/ui/input";
import { EmptyState, ErrorState, LoadingRow, PageHeader } from "@/components/ui/states";
import { Table, TD, TH, THead, TR } from "@/components/ui/table";
import { useSession } from "@/hooks/use-session";
import { api } from "@/lib/api";
import { formatDate, formatRelative } from "@/lib/format";
import { cn } from "@/lib/utils";
import type {
  Page,
  WebhookDelivery,
  WebhookEndpoint,
  WebhookEndpointWithSecret,
  WebhookEventInfo,
} from "@/lib/types";

export default function WebhooksPage() {
  const { projectId } = useSession();
  const queryClient = useQueryClient();
  const [url, setUrl] = useState("");
  const [description, setDescription] = useState("");
  const [selectedEvents, setSelectedEvents] = useState<string[]>([]);
  const [secret, setSecret] = useState<WebhookEndpointWithSecret | null>(null);
  const [filterEndpoint, setFilterEndpoint] = useState<string | null>(null);

  const base = `/v1/projects/${projectId}/webhooks`;

  const endpoints = useQuery({
    queryKey: ["webhooks", projectId],
    queryFn: () => api<WebhookEndpoint[]>(base),
    enabled: Boolean(projectId),
  });

  const events = useQuery({
    queryKey: ["webhook-events", projectId],
    queryFn: () => api<WebhookEventInfo[]>(`${base}/events`),
    enabled: Boolean(projectId),
  });

  const deliveries = useQuery({
    queryKey: ["webhook-deliveries", projectId, filterEndpoint],
    queryFn: () =>
      api<Page<WebhookDelivery>>(`${base}/deliveries`, {
        query: { endpoint_id: filterEndpoint ?? undefined, limit: 50 },
      }),
    enabled: Boolean(projectId),
    refetchInterval: 10_000,
  });

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["webhooks"] });
    queryClient.invalidateQueries({ queryKey: ["webhook-deliveries"] });
  };

  const create = useMutation({
    mutationFn: () =>
      api<WebhookEndpointWithSecret>(base, {
        method: "POST",
        body: { url, description: description || undefined, event_types: selectedEvents },
      }),
    onSuccess: (created) => {
      setSecret(created);
      setUrl("");
      setDescription("");
      setSelectedEvents([]);
      refresh();
    },
  });

  const toggleActive = useMutation({
    mutationFn: ({ id, isActive }: { id: string; isActive: boolean }) =>
      api(`${base}/${id}`, { method: "PATCH", body: { is_active: isActive } }),
    onSuccess: refresh,
  });

  const rotate = useMutation({
    mutationFn: (id: string) =>
      api<WebhookEndpointWithSecret>(`${base}/${id}/rotate-secret`, { method: "POST" }),
    onSuccess: (rotated) => {
      setSecret(rotated);
      refresh();
    },
  });

  const sendTest = useMutation({
    mutationFn: (id: string) => api(`${base}/${id}/test`, { method: "POST" }),
    onSuccess: refresh,
  });

  const remove = useMutation({
    mutationFn: (id: string) => api(`${base}/${id}`, { method: "DELETE" }),
    onSuccess: refresh,
  });

  const retryDelivery = useMutation({
    mutationFn: (id: string) => api(`${base}/deliveries/${id}/retry`, { method: "POST" }),
    onSuccess: refresh,
  });

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="System"
        title="Webhooks"
        description="Outbound notifications, signed with a per-endpoint secret and retried with backoff. Every attempt is recorded."
      />

      {secret && (
        <Card className="border-accent/60 bg-accent-soft shadow-[0_0_0_4px_hsl(var(--accent)/0.08)]">
          <CardHeader>
            <CardTitle className="text-accent">Signing secret</CardTitle>
            <CardDescription>
              Shown once. Your receiver needs it to verify the{" "}
              <code className="font-mono">X-Memora-Signature</code> header.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <code className="block break-all border border-border bg-surface-2 p-3 font-mono text-xs text-accent">
              {secret.secret}
            </code>
            <div className="flex gap-2">
              <Button size="sm" onClick={() => navigator.clipboard.writeText(secret.secret)}>
                Copy
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setSecret(null)}>
                Done
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Add an endpoint</CardTitle>
          <CardDescription>Leave events unselected to receive everything.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <Label htmlFor="hook-url">URL</Label>
              <Input
                id="hook-url"
                placeholder="https://example.com/hooks/memora"
                value={url}
                onChange={(event) => setUrl(event.target.value)}
              />
            </div>
            <div>
              <Label htmlFor="hook-desc">Description</Label>
              <Input
                id="hook-desc"
                placeholder="Support escalations"
                value={description}
                onChange={(event) => setDescription(event.target.value)}
              />
            </div>
          </div>

          <div>
            <Label>Events</Label>
            <div className="grid gap-px border border-border bg-border sm:grid-cols-2">
              {events.data?.map((event) => (
                <button
                  key={event.event}
                  onClick={() =>
                    setSelectedEvents((current) =>
                      current.includes(event.event)
                        ? current.filter((item) => item !== event.event)
                        : [...current, event.event],
                    )
                  }
                  className={cn(
                    "flex items-start gap-3 bg-surface p-3 text-left transition-colors hover:bg-surface-2",
                    selectedEvents.includes(event.event) && "bg-surface-2",
                  )}
                >
                  <span
                    className={cn(
                      "mt-0.5 h-3.5 w-3.5 shrink-0 border",
                      selectedEvents.includes(event.event)
                        ? "border-accent bg-accent"
                        : "border-border-strong bg-surface",
                    )}
                  />
                  <span>
                    <span className="block font-mono text-[11px] uppercase tracking-label">
                      {event.event}
                    </span>
                    <span className="mt-0.5 block text-[11px] text-muted-foreground">
                      {event.description}
                    </span>
                  </span>
                </button>
              ))}
            </div>
          </div>

          <div className="flex items-center gap-3">
            <Button
              onClick={() => create.mutate()}
              loading={create.isPending}
              disabled={!url.startsWith("http")}
            >
              Add endpoint
            </Button>
            {create.error && (
              <p className="font-mono text-[11px] text-danger">
                {create.error instanceof Error ? create.error.message : "Could not add endpoint."}
              </p>
            )}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Endpoints</CardTitle>
        </CardHeader>
        {endpoints.isLoading && <LoadingRow />}
        {endpoints.error && <ErrorState error={endpoints.error} />}
        {endpoints.data?.length === 0 && (
          <EmptyState
            title="No endpoints"
            description="Add one to receive memory and health notifications as they happen."
          />
        )}
        {!!endpoints.data?.length && (
          <div className="space-y-px bg-border">
            {endpoints.data.map((endpoint) => (
              <div key={endpoint.id} className="bg-surface p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <code className="font-mono text-xs">{endpoint.url}</code>
                      <Badge
                        className={
                          endpoint.is_active
                            ? "border-success/70 bg-success/10 text-success"
                            : "border-border bg-surface-2"
                        }
                      >
                        {endpoint.is_active ? "active" : "paused"}
                      </Badge>
                      {endpoint.consecutive_failures > 0 && (
                        <Badge className="border-danger/70 bg-danger/15 text-danger">
                          {endpoint.consecutive_failures} failures
                        </Badge>
                      )}
                    </div>
                    {endpoint.description && (
                      <p className="mt-1 text-xs text-muted-foreground">{endpoint.description}</p>
                    )}
                    <div className="mt-2 flex flex-wrap gap-1">
                      {endpoint.event_types.length === 0 ? (
                        <Badge>all events</Badge>
                      ) : (
                        endpoint.event_types.map((event) => <Badge key={event}>{event}</Badge>)
                      )}
                    </div>
                    <p className="label mt-2">
                      last success {formatRelative(endpoint.last_success_at)} · last failure{" "}
                      {formatRelative(endpoint.last_failure_at)}
                    </p>
                    {endpoint.last_error && (
                      <p className="mt-1 font-mono text-[10px] text-danger">{endpoint.last_error}</p>
                    )}
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    <Button
                      size="sm"
                      variant="outline"
                      loading={sendTest.isPending && sendTest.variables === endpoint.id}
                      onClick={() => sendTest.mutate(endpoint.id)}
                    >
                      Test
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => setFilterEndpoint(endpoint.id)}
                    >
                      Log
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      loading={rotate.isPending && rotate.variables === endpoint.id}
                      onClick={() => rotate.mutate(endpoint.id)}
                    >
                      Rotate
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() =>
                        toggleActive.mutate({ id: endpoint.id, isActive: !endpoint.is_active })
                      }
                    >
                      {endpoint.is_active ? "Pause" : "Resume"}
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      loading={remove.isPending && remove.variables === endpoint.id}
                      onClick={() => remove.mutate(endpoint.id)}
                    >
                      Delete
                    </Button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>

      <Card>
        <CardHeader className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle>Deliveries</CardTitle>
          {filterEndpoint && (
            <Button size="sm" variant="ghost" onClick={() => setFilterEndpoint(null)}>
              Clear filter
            </Button>
          )}
        </CardHeader>
        {deliveries.isLoading && <LoadingRow />}
        {deliveries.data?.data.length === 0 && (
          <EmptyState title="No deliveries yet" description="Send a test to see one here." />
        )}
        {!!deliveries.data?.data.length && (
          <Table className="min-w-[900px]">
            <THead>
              <TR>
                <TH className="w-52">Event</TH>
                <TH className="w-28">Status</TH>
                <TH className="w-20">Tries</TH>
                <TH className="w-28">Response</TH>
                <TH>Detail</TH>
                <TH className="w-32">When</TH>
                <TH className="w-20 text-right" />
              </TR>
            </THead>
            <tbody>
              {deliveries.data.data.map((delivery) => (
                <TR key={delivery.id}>
                  <TD className="font-mono text-[11px]">{delivery.event_type}</TD>
                  <TD>
                    <StatusBadge status={delivery.status} />
                  </TD>
                  <TD className="numeric text-[11px] text-muted-foreground">{delivery.attempts}</TD>
                  <TD className="numeric text-[11px] text-muted-foreground">
                    {delivery.response_status ?? "—"}
                    {delivery.duration_ms != null && (
                      <span className="label"> · {delivery.duration_ms}ms</span>
                    )}
                  </TD>
                  <TD className="max-w-md text-[11px] text-muted-foreground">
                    {delivery.error ?? delivery.response_body ?? "—"}
                  </TD>
                  <TD className="label whitespace-nowrap">{formatDate(delivery.created_at)}</TD>
                  <TD className="text-right">
                    {delivery.status !== "succeeded" && (
                      <Button
                        size="sm"
                        variant="ghost"
                        loading={retryDelivery.isPending && retryDelivery.variables === delivery.id}
                        onClick={() => retryDelivery.mutate(delivery.id)}
                      >
                        Retry
                      </Button>
                    )}
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
