"use client";

import { useQuery } from "@tanstack/react-query";

import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { ApprovalStatus, Decision, GuardrailCatalog, GuardrailReason } from "@/lib/types";

const DECISION_STYLES: Record<Decision, string> = {
  allow: "border-success/60 bg-success/10 text-success",
  require_approval: "border-warning/60 bg-warning/10 text-warning",
  deny: "border-danger/60 bg-danger/10 text-danger",
};

const DECISION_LABELS: Record<Decision, string> = {
  allow: "allowed",
  require_approval: "needs approval",
  deny: "denied",
};

export function DecisionBadge({ decision }: { decision: Decision }) {
  return (
    <Badge className={DECISION_STYLES[decision] ?? ""}>
      {DECISION_LABELS[decision] ?? decision}
    </Badge>
  );
}

const APPROVAL_STYLES: Record<ApprovalStatus, string> = {
  pending: "border-warning/60 bg-warning/10 text-warning",
  approved: "border-success/60 bg-success/10 text-success",
  rejected: "border-danger/60 bg-danger/10 text-danger",
  expired: "border-border text-muted-foreground",
  used: "border-accent/60 bg-accent/10 text-accent",
};

export function ApprovalBadge({ status }: { status: ApprovalStatus }) {
  return <Badge className={APPROVAL_STYLES[status] ?? ""}>{status}</Badge>;
}

const SOURCE_LABELS: Record<string, string> = {
  profile: "profile",
  builtin: "built-in",
  project: "project rule",
  approval: "approval",
};

/** Every reason a check gave, strictest first — the way the API returns them. */
export function ReasonList({
  reasons,
  compact = false,
}: {
  reasons: GuardrailReason[];
  compact?: boolean;
}) {
  if (!reasons.length) {
    return <p className="label">No rule objected.</p>;
  }
  return (
    <ul className="space-y-1.5">
      {reasons.map((reason, index) => (
        <li
          key={`${reason.rule}-${index}`}
          className={cn(
            "border-l-2 pl-2.5",
            reason.decision === "deny"
              ? "border-danger"
              : reason.decision === "require_approval"
                ? "border-warning"
                : "border-border",
          )}
        >
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-[11px] text-foreground">{reason.rule}</span>
            <span className="label">{SOURCE_LABELS[reason.source] ?? reason.source}</span>
            {!compact && reason.decision !== "allow" && (
              <DecisionBadge decision={reason.decision} />
            )}
          </div>
          <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">
            {reason.explanation}
          </p>
          {!compact && reason.evidence.length > 0 && (
            <p className="label mt-1 break-all">evidence: {reason.evidence.join(", ")}</p>
          )}
        </li>
      ))}
    </ul>
  );
}

/** The proposed action's details, as the rules saw them. */
export function RequestSummary({ request }: { request: Record<string, unknown> }) {
  const entries = Object.entries(request ?? {});
  if (!entries.length) return <span className="label">no details</span>;
  return (
    <span className="font-mono text-[11px] text-muted-foreground">
      {entries
        .map(
          ([key, value]) =>
            `${key}=${typeof value === "object" ? JSON.stringify(value) : String(value)}`,
        )
        .join("  ")}
    </span>
  );
}

export function useGuardrailCatalog(projectId: string | null) {
  return useQuery({
    queryKey: ["guardrail-catalog", projectId],
    queryFn: () => api<GuardrailCatalog>(`/v1/projects/${projectId}/agent/guardrails`),
    enabled: Boolean(projectId),
    staleTime: 60 * 60 * 1000,
  });
}
