"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { Fragment, useState } from "react";

import {
  ApprovalBadge,
  DecisionBadge,
  ReasonList,
  RequestSummary,
} from "@/components/agents/shared";
import { Card } from "@/components/ui/card";
import { Input, Select } from "@/components/ui/input";
import { EmptyState, ErrorState, LoadingRow } from "@/components/ui/states";
import { Table, TD, TH, THead, TR } from "@/components/ui/table";
import { api } from "@/lib/api";
import { formatRelative } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { AgentCheck, Decision, Page } from "@/lib/types";

/** Every guardrail check an agent made: what it wanted to do, and what it was told. */
export function ChecksLog({
  projectId,
  customerId,
}: {
  projectId: string | null;
  customerId?: string;
}) {
  const [decision, setDecision] = useState<Decision | "">("");
  const [agent, setAgent] = useState("");
  const [open, setOpen] = useState<string | null>(null);

  const checks = useQuery({
    queryKey: ["agent-checks", projectId, decision, agent, customerId],
    queryFn: () =>
      api<Page<AgentCheck>>(`/v1/projects/${projectId}/agent/checks`, {
        query: {
          decision: decision || undefined,
          agent: agent || undefined,
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
          value={decision}
          onChange={(event) => setDecision(event.target.value as Decision | "")}
          className="w-48"
        >
          <option value="">Every decision</option>
          <option value="deny">Denied</option>
          <option value="require_approval">Needed approval</option>
          <option value="allow">Allowed</option>
        </Select>
        <Input
          value={agent}
          onChange={(event) => setAgent(event.target.value)}
          placeholder="agent or profile name"
          className="w-56"
        />
      </div>
      <Card>
        {checks.isLoading && <LoadingRow />}
        {checks.error && <ErrorState error={checks.error} />}
        {checks.data?.data.length === 0 && (
          <EmptyState
            title="No checks yet"
            description="Agents ask with POST /v1/agent/check (check_action in the SDKs and the MCP server) before they act."
          />
        )}
        {!!checks.data?.data.length && (
          <Table>
            <THead>
              <TR>
                <TH className="w-32">When</TH>
                <TH className="w-36">Agent</TH>
                <TH className="w-40">Customer</TH>
                <TH className="w-40">Action</TH>
                <TH className="w-36">Decision</TH>
                <TH>Why</TH>
              </TR>
            </THead>
            <tbody>
              {checks.data.data.map((check) => (
                <Fragment key={check.id}>
                  <TR
                    className={cn("cursor-pointer", open === check.id && "bg-surface-2")}
                    onClick={() => setOpen(open === check.id ? null : check.id)}
                  >
                    <TD className="label">{formatRelative(check.checked_at)}</TD>
                    <TD className="font-mono text-xs">{check.agent ?? "—"}</TD>
                    <TD>
                      <Link
                        href={`/customers/${check.customer_id}`}
                        onClick={(event) => event.stopPropagation()}
                        className="font-mono text-xs hover:text-accent"
                      >
                        {check.customer_id}
                      </Link>
                    </TD>
                    <TD className="font-mono text-xs">{check.action}</TD>
                    <TD>
                      <div className="flex flex-wrap items-center gap-1">
                        <DecisionBadge decision={check.decision} />
                        {check.approval && <ApprovalBadge status={check.approval.status} />}
                      </div>
                    </TD>
                    <TD className="max-w-xl text-xs leading-relaxed text-muted-foreground">
                      {check.summary}
                    </TD>
                  </TR>
                  {open === check.id && (
                    <TR className="bg-surface-2">
                      <TD colSpan={6}>
                        <div className="space-y-3 py-2">
                          <RequestSummary request={check.request} />
                          <ReasonList reasons={check.reasons} />
                          <p className="label">
                            {check.id}
                            {check.snapshot_id ? ` · state then: ${check.snapshot_id}` : ""}
                            {check.session_id ? ` · session ${check.session_id}` : ""}
                            {check.approval ? ` · approval ${check.approval.id}` : ""}
                          </p>
                        </div>
                      </TD>
                    </TR>
                  )}
                </Fragment>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </div>
  );
}
