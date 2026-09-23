"use client";

import { Badge, MemoryTypeBadge, RestrictedBadge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { EventExplanation, MemoryPlan } from "@/lib/types";

/** How each consolidation action reads, and how alarming it should look.
 *
 * "create" is the only one people expect, so it is the quiet one; everything else changed
 * something that was already there and deserves to be noticed.
 */
const ACTION_STYLES: Record<string, string> = {
  create: "border-success/70 bg-success/10 text-success",
  merge: "border-info/70 bg-info/10 text-info",
  update: "border-info/70 bg-info/10 text-info",
  supersede: "border-warning/70 bg-warning/10 text-warning",
  conflict: "border-danger/70 bg-danger/10 text-danger",
  ignore: "border-border bg-surface-2 text-muted-foreground",
};

/** Why an event did, or would, become a memory.
 *
 * Shared by the dry run and the stored outcome on a processed event, because they are the
 * same thing seen at two moments — showing them differently would imply they might differ.
 */
export function ExplanationPanel({
  explanation,
  className,
}: {
  explanation: EventExplanation;
  className?: string;
}) {
  const stopped = Boolean(explanation.stop_reason);

  return (
    <div className={cn("space-y-3", className)}>
      <div
        className={cn(
          "border-l-2 px-3 py-2",
          stopped ? "border-warning bg-warning/5" : "border-success bg-success/5",
        )}
      >
        <p className="label">{stopped ? "Nothing was remembered" : "Outcome"}</p>
        <p className="mt-1 text-[12px] leading-relaxed">
          {explanation.stop_reason ?? explanation.summary}
        </p>
      </div>

      <div className="flex flex-wrap gap-x-6 gap-y-1">
        <Figure label="Importance" value={explanation.importance.toFixed(2)} />
        <Figure label="Threshold" value={explanation.threshold.toFixed(2)} />
        <Figure label="Text read" value={`${explanation.text_length} chars`} />
        {explanation.duration_ms > 0 && (
          <Figure label="Took" value={`${explanation.duration_ms.toFixed(0)} ms`} />
        )}
      </div>

      {explanation.redacted && (
        <p className="border-l-2 border-border-strong bg-surface-2 px-3 py-2 text-[11px] text-muted-foreground">
          Redacted before extraction:{" "}
          {explanation.redactions
            .map((finding) => `${finding.count} × ${finding.kind.replace(/_/g, " ")}`)
            .join(", ")}
          . The stored event still holds what was sent; the memory never will.
        </p>
      )}

      {explanation.memories.length > 0 && (
        <div className="space-y-px">
          <p className="label mb-1">
            {explanation.memory_count} statement{explanation.memory_count === 1 ? "" : "s"}
          </p>
          {explanation.memories.map((plan, index) => (
            <PlanRow key={`${plan.content}-${index}`} plan={plan} />
          ))}
        </div>
      )}

      {explanation.entities.length > 0 && (
        <div>
          <p className="label mb-1">Entities</p>
          <div className="flex flex-wrap gap-1">
            {explanation.entities.map((entity) => (
              <Badge
                key={`${entity.type}-${entity.name}`}
                className={cn(entity.status === "new" && "border-accent/60 text-accent")}
                title={
                  entity.status === "new"
                    ? "Not yet known to this project"
                    : "Already known to this project"
                }
              >
                {entity.name}
              </Badge>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function Figure({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="label">{label}</p>
      <p className="numeric text-xs font-semibold">{value}</p>
    </div>
  );
}

function PlanRow({ plan }: { plan: MemoryPlan }) {
  return (
    <div className="bg-surface-2 px-3 py-2">
      <div className="flex flex-wrap items-center gap-2">
        <Badge className={ACTION_STYLES[plan.action] ?? ""}>{plan.action}</Badge>
        <MemoryTypeBadge type={plan.type} />
        {plan.sensitivity === "restricted" && <RestrictedBadge reason={plan.restricted_by} />}
        {plan.similarity > 0 && (
          <span className="numeric text-[10px] text-muted-foreground">
            {(plan.similarity * 100).toFixed(0)}% match
          </span>
        )}
        {plan.rule && <span className="label">{plan.rule.replace(/_/g, " ")}</span>}
      </div>

      <p className="mt-1.5 text-[12px] leading-snug">{plan.content}</p>
      <p className="mt-1 text-[11px] text-muted-foreground">{plan.reason}</p>

      {plan.closest_content && (
        <div className="mt-1.5 border-l-2 border-border-strong pl-2">
          {/* On a create this is the nearest miss, which is the more useful reading: it is
              how you find out that a near-duplicate was made because the threshold is set
              higher than the wording in your product actually varies. */}
          <p className="label">
            {plan.action === "create"
              ? "Closest existing memory — not close enough"
              : "Existing memory"}
          </p>
          <p className="text-[11px] text-muted-foreground">{plan.closest_content}</p>
        </div>
      )}

      {plan.extracted_by && (
        <p className="label mt-1.5">read by {plan.extracted_by.replace(/_/g, " ")}</p>
      )}
    </div>
  );
}
