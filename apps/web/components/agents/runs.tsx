"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { DecisionBadge } from "@/components/agents/shared";
import { Badge, MemoryTypeBadge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Select } from "@/components/ui/input";
import { EmptyState, ErrorState, LoadingRow } from "@/components/ui/states";
import { Table, TD, TH, THead, TR } from "@/components/ui/table";
import { api } from "@/lib/api";
import { formatDate, formatRelative } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { AgentRunSummary, Page, RunExplanation } from "@/lib/types";

/** Every answer and briefing an agent asked for — and, opened, why it said what it said. */
export function RunsExplorer({
  projectId,
  customerId,
  initialRun,
}: {
  projectId: string | null;
  customerId?: string;
  initialRun?: string | null;
}) {
  const [agent, setAgent] = useState("");
  const [kind, setKind] = useState<"" | "query" | "context">("");
  const [selected, setSelected] = useState<string | null>(initialRun ?? null);

  const runs = useQuery({
    queryKey: ["agent-runs", projectId, agent, kind, customerId],
    queryFn: () =>
      api<Page<AgentRunSummary>>(`/v1/projects/${projectId}/agent/runs`, {
        query: {
          agent: agent || undefined,
          kind: kind || undefined,
          customer_id: customerId,
          limit: 100,
        },
      }),
    enabled: Boolean(projectId),
  });

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <Select
          value={kind}
          onChange={(event) => setKind(event.target.value as "" | "query" | "context")}
          className="w-44"
        >
          <option value="">Answers and briefings</option>
          <option value="query">Answers</option>
          <option value="context">Briefings</option>
        </Select>
        <Input
          value={agent}
          onChange={(event) => setAgent(event.target.value)}
          placeholder="agent or profile name"
          className="w-56"
        />
      </div>

      <Card>
        {runs.isLoading && <LoadingRow />}
        {runs.error && <ErrorState error={runs.error} />}
        {runs.data?.data.length === 0 && (
          <EmptyState
            title="No runs yet"
            description="Every /v1/memory/query and /v1/memory/context call is recorded as a run, with why each memory was retrieved."
          />
        )}
        {!!runs.data?.data.length && (
          <Table>
            <THead>
              <TR>
                <TH className="w-28">When</TH>
                <TH className="w-32">Agent</TH>
                <TH className="w-36">Customer</TH>
                <TH>Asked</TH>
                <TH className="w-44">Memories</TH>
              </TR>
            </THead>
            <tbody>
              {runs.data.data.map((run) => (
                <TR
                  key={run.id}
                  className={cn("cursor-pointer", selected === run.id && "bg-surface-2")}
                  onClick={() => setSelected(selected === run.id ? null : run.id)}
                >
                  <TD className="label">{formatRelative(run.created_at)}</TD>
                  <TD className="font-mono text-xs">{run.agent ?? "—"}</TD>
                  <TD className="font-mono text-xs">{run.customer_id ?? "—"}</TD>
                  <TD className="max-w-xl">
                    <div className="flex items-center gap-2">
                      <Badge>{run.kind === "query" ? "answer" : "briefing"}</Badge>
                      <span className="truncate text-xs text-foreground">{run.query}</span>
                    </div>
                    {run.answer && (
                      <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-muted-foreground">
                        {run.answer}
                      </p>
                    )}
                  </TD>
                  <TD className="label">
                    {run.memory_count} used · {run.cited_count} cited
                    {run.withheld ? ` · ${run.withheld} withheld` : ""}
                  </TD>
                </TR>
              ))}
            </tbody>
          </Table>
        )}
      </Card>

      {selected && projectId && (
        <RunExplanationCard
          projectId={projectId}
          runId={selected}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  );
}

export function RunExplanationCard({
  projectId,
  runId,
  onClose,
}: {
  projectId: string;
  runId: string;
  onClose?: () => void;
}) {
  const explanation = useQuery({
    queryKey: ["agent-run-explain", projectId, runId],
    queryFn: () => api<RunExplanation>(`/v1/projects/${projectId}/agent/runs/${runId}/explain`),
  });
  const data = explanation.data;

  return (
    <Card>
      <CardHeader className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <CardTitle>Why the agent said this</CardTitle>
          <p className="mt-1 font-mono text-[11px] text-muted-foreground">{runId}</p>
        </div>
        {onClose && (
          <button className="label hover:text-accent" onClick={onClose}>
            Close
          </button>
        )}
      </CardHeader>
      <CardContent className="space-y-5">
        {explanation.isLoading && <LoadingRow />}
        {explanation.error && <ErrorState error={explanation.error} />}
        {data && (
          <>
            <ol className="space-y-1.5 border-l-2 border-accent pl-3">
              {data.narrative.map((line, index) => (
                <li key={index} className="text-xs leading-relaxed">
                  {line}
                </li>
              ))}
            </ol>

            {data.run.answer && (
              <div className="border border-border bg-surface-2 px-4 py-3">
                <p className="label-strong">The answer</p>
                <p className="mt-1.5 text-xs leading-relaxed">{data.run.answer}</p>
              </div>
            )}

            <div>
              <p className="label mb-2">Memories, as the agent saw them and as they are now</p>
              <Table>
                <THead>
                  <TR>
                    <TH className="w-10">#</TH>
                    <TH className="w-28">Type</TH>
                    <TH>Then</TH>
                    <TH>Now</TH>
                    <TH className="w-40">Why retrieved</TH>
                  </TR>
                </THead>
                <tbody>
                  {data.memories.map((memory) => (
                    <TR key={memory.id}>
                      <TD className="numeric text-xs">
                        {memory.rank}
                        {memory.cited && (
                          <span className="ml-1 text-accent" title="cited in the answer">
                            ●
                          </span>
                        )}
                      </TD>
                      <TD>{memory.type ? <MemoryTypeBadge type={memory.type} /> : "—"}</TD>
                      <TD className="max-w-sm text-xs leading-relaxed">
                        {memory.visible ? (
                          (memory.content_then ?? "—")
                        ) : (
                          <span className="label">withheld from you</span>
                        )}
                        <p className="label mt-1">{memory.id}</p>
                      </TD>
                      <TD className="max-w-sm text-xs leading-relaxed">
                        {!memory.visible ? (
                          <span className="label">withheld</span>
                        ) : memory.changed_since.length ? (
                          <>
                            <span className="text-warning">{memory.content_now ?? "deleted"}</span>
                            <p className="label mt-1">
                              {memory.changed_since
                                .map((change) => `${change.reason} ${formatDate(change.at)}`)
                                .join(" · ")}
                            </p>
                          </>
                        ) : (
                          <span className="label">
                            {memory.status_now === "active" ? "unchanged" : memory.status_now}
                          </span>
                        )}
                      </TD>
                      <TD className="label">
                        {memory.strategies.join(", ") || "—"}
                        <p className="numeric mt-1">score {memory.score.toFixed(3)}</p>
                      </TD>
                    </TR>
                  ))}
                </tbody>
              </Table>
            </div>

            <div className="grid gap-4 md:grid-cols-2">
              <div className="border border-border px-4 py-3">
                <p className="label-strong">Held back</p>
                <ul className="mt-2 space-y-1 text-xs text-muted-foreground">
                  <li>{data.held_back.withheld ?? 0} memories not visible to the caller</li>
                  <li>profile: {data.held_back.profile ?? "none"}</li>
                  <li>
                    memory types:{" "}
                    {data.held_back.readable_types?.length
                      ? data.held_back.readable_types.join(", ")
                      : "all"}
                  </li>
                  <li>restricted clearance: {data.held_back.cleared ? "yes" : "no"}</li>
                  {!!data.held_back.dropped_by_budget?.length && (
                    <li>
                      {data.held_back.dropped_by_budget.length} dropped to fit the token budget
                    </li>
                  )}
                </ul>
              </div>
              <div className="border border-border px-4 py-3">
                <p className="label-strong">The customer then</p>
                {data.state_then ? (
                  <ul className="mt-2 space-y-1 text-xs text-muted-foreground">
                    <li>
                      health {data.state_then.health_score?.toFixed(0) ?? "—"} (
                      {data.state_then.health_band ?? "—"})
                    </li>
                    <li>lifecycle {data.state_then.state ?? "—"}</li>
                    <li>{data.state_then.open_problems} open problems</li>
                    <li className="label">snapshot {data.state_then.id}</li>
                  </ul>
                ) : (
                  <p className="label mt-2">No snapshot had been taken yet.</p>
                )}
              </div>
            </div>

            {data.checks.length > 0 && (
              <div>
                <p className="label mb-2">Guardrail checks around this run</p>
                <ul className="space-y-1.5">
                  {data.checks.map((check) => (
                    <li key={check.id} className="flex flex-wrap items-center gap-2 text-xs">
                      <span className="font-mono">{check.action}</span>
                      <DecisionBadge decision={check.decision} />
                      <span className="text-muted-foreground">{check.summary}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {data.run.customer_id && (
              <Link href={`/customers/${data.run.customer_id}`} className="label hover:text-accent">
                Open {data.run.customer_id} →
              </Link>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}
