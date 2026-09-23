"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { GoalStatusBadge } from "@/components/foresight";
import { Card } from "@/components/ui/card";
import { Select } from "@/components/ui/input";
import { EmptyState, ErrorState, LoadingRow, PageHeader } from "@/components/ui/states";
import { Table, TD, TH, THead, TR } from "@/components/ui/table";
import { useSession } from "@/hooks/use-session";
import { api } from "@/lib/api";
import { formatRelative } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { Goal, GoalStatus, GoalSummary, Page as Paged } from "@/lib/types";

const FILTERS: { label: string; value: GoalStatus | "" }[] = [
  { label: "Every goal", value: "" },
  { label: "Open", value: "open" },
  { label: "Progressing", value: "progressing" },
  { label: "Stalled", value: "stalled" },
  { label: "Achieved", value: "achieved" },
  { label: "Abandoned", value: "abandoned" },
];

const OVERRIDES: GoalStatus[] = ["progressing", "achieved", "abandoned"];

export default function GoalsPage() {
  const { projectId } = useSession();
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<GoalStatus | "">("");

  const goals = useQuery({
    queryKey: ["goals", projectId, status],
    queryFn: () =>
      api<Paged<Goal>>(`/v1/projects/${projectId}/goals`, {
        query: { status: status || undefined, limit: 100 },
      }),
    enabled: Boolean(projectId),
  });

  const summary = useQuery({
    queryKey: ["goals-summary", projectId],
    queryFn: () => api<GoalSummary>(`/v1/projects/${projectId}/goals/summary`),
    enabled: Boolean(projectId),
  });

  const override = useMutation({
    mutationFn: (input: { goal: Goal; next: GoalStatus }) =>
      api<Goal>(`/v1/projects/${projectId}/goals/${input.goal.id}`, {
        method: "PATCH",
        body: { status: input.next, note: "Set from the dashboard" },
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["goals", projectId] });
      queryClient.invalidateQueries({ queryKey: ["goals-summary", projectId] });
    },
  });

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Memory"
        title="Goals"
        description="What customers said they were trying to do, tracked until something in their history says it happened — or stopped."
        actions={
          <Select
            value={status}
            onChange={(event) => setStatus(event.target.value as GoalStatus | "")}
            className="w-48"
          >
            {FILTERS.map((filter) => (
              <option key={filter.label} value={filter.value}>
                {filter.label}
              </option>
            ))}
          </Select>
        }
      />

      {summary.data && (
        <div className="grid gap-px overflow-hidden rounded-lg border border-border bg-border sm:grid-cols-5">
          {(["open", "progressing", "stalled", "achieved", "abandoned"] as const).map((key) => (
            <div key={key} className="bg-surface px-4 py-3.5">
              <p className="label">{key}</p>
              <p className="figure mt-2 text-[1.75rem]">
                {summary.data[key]}
              </p>
            </div>
          ))}
        </div>
      )}

      {override.error && <ErrorState error={override.error} />}

      <Card>
        {goals.isLoading && <LoadingRow />}
        {goals.error && <ErrorState error={goals.error} />}
        {goals.data?.data.length === 0 && (
          <EmptyState
            title="No goals yet"
            description="A goal is opened the moment a customer says what they are trying to do."
          />
        )}
        {!!goals.data?.data.length && (
          <Table>
            <THead>
              <TR>
                <TH>Goal</TH>
                <TH className="w-28">Status</TH>
                <TH className="w-36">Progress</TH>
                <TH className="w-40">Last signal</TH>
                <TH className="w-56">Set status</TH>
              </TR>
            </THead>
            <tbody>
              {goals.data.data.map((goal) => (
                <TR key={goal.id}>
                  <TD className="max-w-xl">
                    <p className="text-sm leading-relaxed">{goal.statement}</p>
                    <Link
                      href={`/customers/${goal.customer_id}`}
                      className="label mt-1 inline-block hover:text-accent"
                    >
                      {goal.customer_id}
                    </Link>
                  </TD>
                  <TD>
                    <GoalStatusBadge status={goal.status} />
                    {goal.overridden && <p className="label mt-1">by a person</p>}
                  </TD>
                  <TD>
                    <div className="flex items-center gap-2">
                      <div className="h-1.5 w-20 overflow-hidden rounded-full border border-border bg-surface-3">
                        <div
                          className={cn(
                            "h-full",
                            goal.status === "achieved" ? "bg-success" : "bg-accent",
                          )}
                          style={{ width: `${Math.round(goal.progress * 100)}%` }}
                        />
                      </div>
                      <span className="numeric label">{Math.round(goal.progress * 100)}%</span>
                    </div>
                  </TD>
                  <TD className="label">{formatRelative(goal.last_signal_at)}</TD>
                  <TD>
                    <div className="flex flex-wrap gap-px overflow-hidden rounded-md border border-border bg-border">
                      {OVERRIDES.map((next) => (
                        <button
                          key={next}
                          disabled={goal.status === next || override.isPending}
                          onClick={() => override.mutate({ goal, next })}
                          className={cn(
                            "px-2 py-1 font-mono text-[10px] uppercase tracking-label transition-colors",
                            "bg-surface text-muted-foreground hover:bg-surface-2 hover:text-foreground",
                            "disabled:cursor-not-allowed disabled:opacity-50",
                          )}
                        >
                          {next}
                        </button>
                      ))}
                    </div>
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
