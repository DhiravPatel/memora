"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Select } from "@/components/ui/input";
import { EmptyState, ErrorState, LoadingRow } from "@/components/ui/states";
import { Table, TD, TH, THead, TR } from "@/components/ui/table";
import { api } from "@/lib/api";
import { formatDate, formatRelative } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { AgentSession, Page as Paged } from "@/lib/types";

const FILTERS = [
  { label: "Every session", value: "" },
  { label: "Open", value: "open" },
  { label: "Closed", value: "closed" },
  { label: "Expired", value: "expired" },
];

const STATUS_STYLES: Record<string, string> = {
  open: "border-accent/70 bg-accent/10 text-accent",
  closed: "border-success/70 bg-success/10 text-success",
  expired: "border-warning/70 bg-warning/10 text-warning",
};

/** Conversations an agent had, with the memory they carried in and out (§13e). */
export function SessionsList({
  projectId,
  customerId,
}: {
  projectId: string | null;
  customerId?: string;
}) {
  const [status, setStatus] = useState("");
  const [selected, setSelected] = useState<string | null>(null);

  const sessions = useQuery({
    queryKey: ["agent-sessions", projectId, status, customerId],
    queryFn: () =>
      api<Paged<AgentSession>>(`/v1/projects/${projectId}/agent/sessions`, {
        query: { status: status || undefined, customer_id: customerId, limit: 100 },
      }),
    enabled: Boolean(projectId),
  });

  const detail = useQuery({
    queryKey: ["agent-session", projectId, selected],
    queryFn: () => api<AgentSession>(`/v1/projects/${projectId}/agent/sessions/${selected}`),
    enabled: Boolean(projectId && selected),
  });

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="max-w-2xl text-xs leading-relaxed text-muted-foreground">
          Each session is briefed from memory on the way in and writes a summary back on the way out
          — that summary is what the next conversation reads.
        </p>
        <Select value={status} onChange={(event) => setStatus(event.target.value)} className="w-48">
          {FILTERS.map((filter) => (
            <option key={filter.label} value={filter.value}>
              {filter.label}
            </option>
          ))}
        </Select>
      </div>

      <Card>
        {sessions.isLoading && <LoadingRow />}
        {sessions.error && <ErrorState error={sessions.error} />}
        {sessions.data?.data.length === 0 && (
          <EmptyState
            title="No agent sessions yet"
            description="Open one with POST /v1/agent/sessions, or memory.agent.open() in the SDK."
          />
        )}
        {!!sessions.data?.data.length && (
          <Table>
            <THead>
              <TR>
                <TH className="w-40">Agent</TH>
                <TH className="w-48">Customer</TH>
                <TH className="w-24">Status</TH>
                <TH className="w-20">Turns</TH>
                <TH className="w-40">Last active</TH>
                <TH>What it established</TH>
              </TR>
            </THead>
            <tbody>
              {sessions.data.data.map((session) => (
                <TR
                  key={session.id}
                  className={cn("cursor-pointer", selected === session.id && "bg-surface-2")}
                  onClick={() => setSelected(session.id === selected ? null : session.id)}
                >
                  <TD>
                    <p className="font-mono text-xs text-foreground">{session.agent}</p>
                    <p className="label mt-0.5">{session.channel ?? "api"}</p>
                  </TD>
                  <TD>
                    <Link
                      href={`/customers/${session.customer_id}`}
                      onClick={(event) => event.stopPropagation()}
                      className="font-mono text-xs hover:text-accent"
                    >
                      {session.customer_id}
                    </Link>
                  </TD>
                  <TD>
                    <Badge className={STATUS_STYLES[session.status] ?? ""}>{session.status}</Badge>
                  </TD>
                  <TD className="numeric text-sm">{session.turn_count}</TD>
                  <TD className="label">{formatRelative(session.last_active_at)}</TD>
                  <TD className="max-w-xl text-xs leading-relaxed text-muted-foreground">
                    {session.summary ?? (
                      <span className="label">
                        {session.status === "open" ? "still open" : "no summary written"}
                      </span>
                    )}
                  </TD>
                </TR>
              ))}
            </tbody>
          </Table>
        )}
      </Card>

      {selected && (
        <Card>
          <CardHeader className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <CardTitle>Transcript</CardTitle>
              <p className="mt-1 text-xs text-muted-foreground">
                Customer turns become memory; the agent&apos;s own words are recorded but never
                learned from.
              </p>
            </div>
            <button className="label hover:text-accent" onClick={() => setSelected(null)}>
              Close
            </button>
          </CardHeader>
          <CardContent className="space-y-3">
            {detail.isLoading && <LoadingRow />}
            {detail.error && <ErrorState error={detail.error} />}
            {detail.data?.turns.length === 0 && (
              <p className="label">Nothing was said in this session.</p>
            )}
            {detail.data?.turns.map((turn) => (
              <div
                key={turn.id}
                className={cn(
                  "border-l-2 px-3 py-2",
                  turn.role === "user" ? "border-accent bg-surface-2" : "border-border",
                )}
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="label-strong">{turn.role}</span>
                  <span className="label">{formatDate(turn.occurred_at)}</span>
                  {turn.event_id && <span className="label text-success">remembered</span>}
                  {turn.retrieved_memory_ids.length > 0 && (
                    <span className="label">
                      {turn.retrieved_memory_ids.length} memories retrieved
                    </span>
                  )}
                </div>
                <p className="mt-1.5 text-xs leading-relaxed">{turn.content}</p>
              </div>
            ))}

            {detail.data?.summary && (
              <div className="border border-border-strong bg-surface-2 px-4 py-3">
                <p className="label-strong">Written back to memory</p>
                <p className="mt-1.5 text-xs leading-relaxed">{detail.data.summary}</p>
                {detail.data.summary_memory_id && (
                  <p className="label mt-2">{detail.data.summary_memory_id}</p>
                )}
              </div>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
