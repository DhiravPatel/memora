"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { ApprovalBadge, ReasonList, RequestSummary } from "@/components/agents/shared";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Select } from "@/components/ui/input";
import { EmptyState, ErrorState, LoadingRow } from "@/components/ui/states";
import { api } from "@/lib/api";
import { formatDate, formatRelative } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { ActionStatus, AgentActionRecord, Page } from "@/lib/types";

const STATUS_STYLES: Record<ActionStatus, string> = {
  allowed: "border-accent/60 bg-accent/10 text-accent",
  pending_approval: "border-warning/60 bg-warning/10 text-warning",
  denied: "border-danger/60 bg-danger/10 text-danger",
  done: "border-success/60 bg-success/10 text-success",
  failed: "border-danger/60 bg-danger/10 text-danger",
  cancelled: "border-border text-muted-foreground",
  expired: "border-border text-muted-foreground",
};

const FILTERS: { label: string; value: ActionStatus | "" }[] = [
  { label: "Every action", value: "" },
  { label: "Done", value: "done" },
  { label: "Allowed, not reported yet", value: "allowed" },
  { label: "Waiting for a person", value: "pending_approval" },
  { label: "Denied", value: "denied" },
  { label: "Failed", value: "failed" },
  { label: "Cancelled", value: "cancelled" },
  { label: "Expired", value: "expired" },
];

/** What agents asked to do through the gateway, and what became of it (§26 4.5).
 *
 * This is the customer's action history — what `actions.*` facts count, and so what limits
 * like "a third credit this month needs a person" read.
 */
export function ActionsLog({
  projectId,
  customerId,
}: {
  projectId: string | null;
  customerId?: string;
}) {
  const [status, setStatus] = useState<ActionStatus | "">("");
  const [open, setOpen] = useState<string | null>(null);
  const actions = useQuery({
    queryKey: ["agent-actions", projectId, status, customerId],
    queryFn: () =>
      api<Page<AgentActionRecord>>(`/v1/projects/${projectId}/agent/actions`, {
        query: { status: status || undefined, customer_id: customerId, limit: 100 },
      }),
    enabled: Boolean(projectId),
  });

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="max-w-2xl text-xs leading-relaxed text-muted-foreground">
          Agents request an action before taking it and report back after. What was allowed or done
          is the customer&apos;s action history, which rules and automatic limits read.
        </p>
        <Select
          value={status}
          onChange={(event) => setStatus(event.target.value as ActionStatus | "")}
          className="w-56"
        >
          {FILTERS.map((filter) => (
            <option key={filter.label} value={filter.value}>
              {filter.label}
            </option>
          ))}
        </Select>
      </div>
      <Card>
        {actions.isLoading && <LoadingRow />}
        {actions.error && <ErrorState error={actions.error} />}
        {actions.data?.data.length === 0 && (
          <EmptyState
            title="No actions yet"
            description="Actions appear when an agent calls POST /v1/agent/actions/request — or MemoryAgent.perform — before it acts."
          />
        )}
        <div className="divide-y divide-border">
          {actions.data?.data.map((item) => (
            <div key={item.id} className="px-5 py-3">
              <button
                className="flex w-full flex-wrap items-center gap-2 text-left"
                onClick={() => setOpen(open === item.id ? null : item.id)}
              >
                <span className="font-mono text-sm text-foreground">{item.action}</span>
                <Badge className={cn(STATUS_STYLES[item.status])}>
                  {item.status.replace(/_/g, " ")}
                </Badge>
                {!customerId && (
                  <Link
                    href={`/customers/${item.customer_id}`}
                    onClick={(event) => event.stopPropagation()}
                    className="font-mono text-xs hover:text-accent"
                  >
                    {item.customer_id}
                  </Link>
                )}
                {item.agent && <span className="label">by {item.agent}</span>}
                <span className="label ml-auto">{formatRelative(item.created_at)}</span>
              </button>
              <p className="mt-1 text-xs text-muted-foreground">{item.summary}</p>
              {open === item.id && (
                <div className="mt-3 space-y-2">
                  <RequestSummary request={item.request} />
                  <ReasonList reasons={item.reasons} />
                  {item.approval && (
                    <p className="flex items-center gap-2 text-xs">
                      <span className="label">approval</span>
                      <ApprovalBadge status={item.approval.status} />
                      {item.approval.note && (
                        <span className="text-muted-foreground">“{item.approval.note}”</span>
                      )}
                    </p>
                  )}
                  <p className="label">
                    {item.completed_at
                      ? `reported ${formatDate(item.completed_at)}`
                      : item.next_step}
                    {item.external_ref ? ` · ${item.external_ref}` : ""}
                    {item.outcome_note ? ` · “${item.outcome_note}”` : ""}
                    {item.idempotency_key ? ` · key ${item.idempotency_key}` : ""}
                  </p>
                </div>
              )}
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
