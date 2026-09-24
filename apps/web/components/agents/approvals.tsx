"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { ApprovalBadge, ReasonList, RequestSummary } from "@/components/agents/shared";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Select, Textarea } from "@/components/ui/input";
import { EmptyState, ErrorState, LoadingRow } from "@/components/ui/states";
import { api } from "@/lib/api";
import { formatDate, formatRelative } from "@/lib/format";
import type { Approval, ApprovalStatus, Page } from "@/lib/types";

const FILTERS: { label: string; value: ApprovalStatus | "" }[] = [
  { label: "Waiting for a person", value: "pending" },
  { label: "Approved, not yet used", value: "approved" },
  { label: "Used", value: "used" },
  { label: "Rejected", value: "rejected" },
  { label: "Expired", value: "expired" },
  { label: "Everything", value: "" },
];

/** The queue of actions agents asked permission for.
 *
 * Deciding is a judgement about one customer, so each request shows why it was raised —
 * the rules that asked for a person and the evidence behind them — next to the buttons.
 * An approval covers exactly the request shown, once; the agent redeems it itself.
 */
export function ApprovalsQueue({
  projectId,
  customerId,
}: {
  projectId: string | null;
  customerId?: string;
}) {
  const client = useQueryClient();
  const [status, setStatus] = useState<ApprovalStatus | "">("pending");
  const [notes, setNotes] = useState<Record<string, string>>({});

  const approvals = useQuery({
    queryKey: ["agent-approvals", projectId, status, customerId],
    queryFn: () =>
      api<Page<Approval>>(`/v1/projects/${projectId}/agent/approvals`, {
        query: { status: status || undefined, customer_id: customerId, limit: 100 },
      }),
    enabled: Boolean(projectId),
    refetchInterval: status === "pending" ? 15_000 : false,
  });

  const decide = useMutation({
    mutationFn: ({ id, decision }: { id: string; decision: "approve" | "reject" }) =>
      api<Approval>(`/v1/projects/${projectId}/agent/approvals/${id}/decision`, {
        method: "POST",
        body: { decision, note: notes[id] || undefined },
      }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["agent-approvals", projectId] });
      client.invalidateQueries({ queryKey: ["agent-activity", projectId] });
    },
  });

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="max-w-2xl text-xs leading-relaxed text-muted-foreground">
          An agent that hits a rule needing a person files a request here. Approving lets the agent
          redeem it once, for exactly this request — and only if no rule denies it by then.
        </p>
        <Select
          value={status}
          onChange={(event) => setStatus(event.target.value as ApprovalStatus | "")}
          className="w-56"
        >
          {FILTERS.map((filter) => (
            <option key={filter.label} value={filter.value}>
              {filter.label}
            </option>
          ))}
        </Select>
      </div>

      {decide.error && <ErrorState error={decide.error} />}
      <Card>
        {approvals.isLoading && <LoadingRow />}
        {approvals.error && <ErrorState error={approvals.error} />}
        {approvals.data?.data.length === 0 && (
          <EmptyState
            title={status === "pending" ? "Nothing is waiting" : "No requests"}
            description="Requests appear when a guardrail check says an action needs a person — a discount, a refund, closing a ticket whose problem is still open."
          />
        )}
        <div className="divide-y divide-border">
          {approvals.data?.data.map((approval) => (
            <div key={approval.id} className="grid gap-4 px-5 py-4 lg:grid-cols-[1fr_20rem]">
              <div className="min-w-0 space-y-2">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-sm text-foreground">{approval.action}</span>
                  <ApprovalBadge status={approval.status} />
                  <span className="label">for</span>
                  <Link
                    href={`/customers/${approval.customer_id}`}
                    className="font-mono text-xs hover:text-accent"
                  >
                    {approval.customer_id}
                  </Link>
                  {approval.agent && <span className="label">by {approval.agent}</span>}
                </div>
                <RequestSummary request={approval.request} />
                <ReasonList reasons={approval.reasons} />
                <p className="label">
                  asked {formatRelative(approval.created_at)} ·{" "}
                  {approval.status === "pending"
                    ? `lapses ${formatRelative(approval.expires_at)}`
                    : approval.decided_at
                      ? `decided ${formatDate(approval.decided_at)}`
                      : `expires ${formatDate(approval.expires_at)}`}
                  {approval.note ? ` · “${approval.note}”` : ""}
                </p>
              </div>
              {approval.status === "pending" ? (
                <div className="space-y-2">
                  <Textarea
                    rows={2}
                    placeholder="Note for the agent and the audit log (optional)"
                    value={notes[approval.id] ?? ""}
                    onChange={(event) => setNotes({ ...notes, [approval.id]: event.target.value })}
                    className="text-xs"
                  />
                  <div className="flex gap-2">
                    <Button
                      size="sm"
                      loading={
                        decide.isPending &&
                        decide.variables?.id === approval.id &&
                        decide.variables.decision === "approve"
                      }
                      onClick={() => decide.mutate({ id: approval.id, decision: "approve" })}
                    >
                      Approve
                    </Button>
                    <Button
                      size="sm"
                      variant="danger"
                      loading={
                        decide.isPending &&
                        decide.variables?.id === approval.id &&
                        decide.variables.decision === "reject"
                      }
                      onClick={() => decide.mutate({ id: approval.id, decision: "reject" })}
                    >
                      Reject
                    </Button>
                  </div>
                </div>
              ) : (
                <div className="label self-start">
                  {approval.decided_by ? `by ${approval.decided_by}` : ""}
                  {approval.used_at ? ` · used ${formatRelative(approval.used_at)}` : ""}
                </div>
              )}
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
