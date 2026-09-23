"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useState } from "react";

import { EventDryRun } from "@/components/event-dry-run";
import { ExplanationPanel } from "@/components/event-explanation";
import { ScoreBar, StatusBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input, Select } from "@/components/ui/input";
import { EmptyState, ErrorState, LoadingRow, PageHeader } from "@/components/ui/states";
import { Table, TD, TH, THead, TR } from "@/components/ui/table";
import { useEventStream, type StreamedEvent } from "@/hooks/use-event-stream";
import { useSession } from "@/hooks/use-session";
import { api } from "@/lib/api";
import { formatDate, formatRelative } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { EventRecord, Page } from "@/lib/types";

export default function EventsPage() {
  const { projectId } = useSession();
  const queryClient = useQueryClient();
  const [status, setStatus] = useState("");
  const [eventType, setEventType] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [live, setLive] = useState(true);
  const [dryRun, setDryRun] = useState(false);
  // Ids that arrived on the stream, so a new row can be marked as it lands.
  const [streamed, setStreamed] = useState<Set<string>>(() => new Set());

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["events"] });

  /* Updating the cached page in place keeps the table from flickering, and means an event
     appears the moment it is ingested rather than on the next poll. */
  const onStreamed = useCallback(
    (incoming: StreamedEvent) => {
      setStreamed((current) => new Set(current).add(incoming.id));
      queryClient.setQueriesData<Page<EventRecord>>(
        { queryKey: ["events", projectId] },
        (current) => {
          if (!current) return current;
          const { change, ...event } = incoming;
          const existing = current.data.findIndex((item) => item.id === event.id);
          if (existing >= 0) {
            const data = [...current.data];
            data[existing] = { ...data[existing]!, ...event };
            return { ...current, data };
          }
          return { ...current, data: [event, ...current.data], total: current.total + 1 };
        },
      );
    },
    [projectId, queryClient],
  );

  const stream = useEventStream(projectId, onStreamed, { enabled: live });

  const retryOne = useMutation({
    mutationFn: (eventId: string) =>
      api(`/v1/projects/${projectId}/events/${eventId}/retry`, { method: "POST" }),
    onSuccess: invalidate,
  });

  const retryAll = useMutation({
    mutationFn: () => api(`/v1/projects/${projectId}/events/retry-failed`, { method: "POST" }),
    onSuccess: invalidate,
  });

  const events = useQuery({
    queryKey: ["events", projectId, status, eventType],
    queryFn: () =>
      api<Page<EventRecord>>(`/v1/projects/${projectId}/events`, {
        query: { status: status || undefined, event_type: eventType || undefined, limit: 100 },
      }),
    enabled: Boolean(projectId),
    // The stream carries new events; polling is the fallback for when it is switched off
    // or cannot connect.
    refetchInterval: live && stream.status === "live" ? false : 15_000,
  });

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Pipeline"
        title="Event explorer"
        description={
          stream.lastEventAt && live
            ? `Raw events exactly as they were received, immutable, with their processing status. Last event ${formatRelative(stream.lastEventAt.toISOString())}.`
            : "Raw events exactly as they were received, immutable, with their processing status."
        }
        actions={
          <>
            <button
              onClick={() => setLive((current) => !current)}
              title={live ? "Stop following new events" : "Follow new events as they arrive"}
              className={cn(
                "flex items-center gap-2 border px-2.5 py-1 font-mono text-[10px] uppercase tracking-label transition-colors",
                live && stream.status === "live"
                  ? "border-success/70 bg-success/10 text-success"
                  : live
                    ? "border-warning/70 bg-warning/10 text-warning"
                    : "border-border-strong bg-surface-2 text-muted-foreground",
              )}
            >
              <span
                className={cn(
                  "h-1.5 w-1.5",
                  live && stream.status === "live"
                    ? "animate-pulse bg-success"
                    : live
                      ? "bg-warning"
                      : "bg-muted-foreground",
                )}
              />
              {live ? (stream.status === "live" ? "Live" : "Connecting") : "Paused"}
            </button>
            <Button
              variant={dryRun ? "primary" : "outline"}
              size="sm"
              onClick={() => setDryRun((current) => !current)}
              title="See what an event would do, without sending it"
            >
              Dry run
            </Button>
            <Button
              variant="secondary"
              size="sm"
              loading={retryAll.isPending}
              onClick={() => retryAll.mutate()}
              title="Re-queue every failed event in this project"
            >
              Retry failed
            </Button>
            <Input
              placeholder="EVENT TYPE…"
              className="w-48 uppercase tracking-label"
              value={eventType}
              onChange={(event) => setEventType(event.target.value)}
            />
            <Select
              value={status}
              onChange={(event) => setStatus(event.target.value)}
              className="w-40"
            >
              <option value="">Any status</option>
              <option value="pending">Pending</option>
              <option value="processing">Processing</option>
              <option value="processed">Processed</option>
              <option value="skipped">Skipped</option>
              <option value="failed">Failed</option>
            </Select>
          </>
        }
      />

      {dryRun && <EventDryRun projectId={projectId} />}

      <Card>
        {events.isLoading && <LoadingRow />}
        {events.error && <ErrorState error={events.error} />}
        {events.data?.data.length === 0 && (
          <EmptyState title="No events" description="Send your first event to POST /v1/events." />
        )}
        {!!events.data?.data.length && (
          <Table className="min-w-[980px]">
            <THead>
              <TR>
                <TH>Event</TH>
                <TH className="w-56">Customer</TH>
                <TH className="w-36">Importance</TH>
                <TH className="min-w-[220px]">Result</TH>
                <TH className="w-36">Occurred</TH>
                <TH className="w-28">Status</TH>
                <TH className="w-20" />
              </TR>
            </THead>
            <tbody>
              {events.data.data.map((event) => (
                <TR key={event.id} className={cn(streamed.has(event.id) && "bg-success/5")}>
                  <TD>
                    <button
                      className="text-left font-mono text-[11px] uppercase tracking-label text-foreground hover:text-accent"
                      onClick={() => setExpanded(expanded === event.id ? null : event.id)}
                    >
                      {event.event_type}
                    </button>
                    {expanded === event.id && (
                      <div className="mt-2 max-w-xl space-y-3">
                        <pre className="overflow-auto border border-border bg-surface-2 p-3 font-mono text-[10px] leading-relaxed text-muted-foreground">
                          {JSON.stringify(event.data, null, 2)}
                        </pre>
                        {event.outcome ? (
                          <ExplanationPanel explanation={event.outcome} />
                        ) : (
                          <p className="label">
                            {event.status === "pending" || event.status === "processing"
                              ? "Not processed yet."
                              : "No record of what happened — this event predates outcome tracking. Retry it to find out."}
                          </p>
                        )}
                      </div>
                    )}
                    {event.error && (
                      <p className="mt-1 font-mono text-[10px] text-danger">{event.error}</p>
                    )}
                  </TD>
                  <TD className="label">{event.customer_id}</TD>
                  <TD>
                    <ScoreBar value={event.importance} />
                  </TD>
                  <TD>
                    {event.outcome ? (
                      <button
                        className="text-left text-[11px] leading-snug text-muted-foreground hover:text-accent"
                        onClick={() => setExpanded(expanded === event.id ? null : event.id)}
                        title="Show the decisions behind this"
                      >
                        {event.outcome.stop_reason ?? event.outcome.summary}
                      </button>
                    ) : (
                      <span className="label">—</span>
                    )}
                  </TD>
                  <TD className="label whitespace-nowrap">{formatDate(event.occurred_at)}</TD>
                  <TD>
                    <StatusBadge status={event.status} />
                  </TD>
                  <TD>
                    {event.status === "failed" || event.status === "pending" ? (
                      <Button
                        size="sm"
                        variant="outline"
                        loading={retryOne.isPending && retryOne.variables === event.id}
                        onClick={() => retryOne.mutate(event.id)}
                      >
                        Retry
                      </Button>
                    ) : null}
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
