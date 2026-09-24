"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { ConditionEditor, ConditionTrace, useFactCatalog } from "@/components/condition-editor";
import { FactsTable } from "@/components/lifecycle";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorState, LoadingRow } from "@/components/ui/states";
import { api } from "@/lib/api";
import { formatRelative } from "@/lib/format";
import type { ConditionEvaluationResult, ConditionValidation, CustomerFacts } from "@/lib/types";

/** Every fact a rule can read about this customer, and a place to try a rule on them.
 *
 * The tester is the point: before a guardrail, a transition or a workflow is saved, the
 * person writing it can see whether it fires for a real customer — and exactly which
 * clause decided it.
 */
export function FactsPanel({
  projectId,
  customerId,
}: {
  projectId: string | null;
  customerId: string;
}) {
  const facts = useQuery({
    queryKey: ["customer-facts", projectId, customerId],
    queryFn: () => api<CustomerFacts>(`/v1/projects/${projectId}/customers/${customerId}/facts`),
    enabled: Boolean(projectId),
  });
  const catalog = useFactCatalog(projectId);
  const [condition, setCondition] = useState("");
  const [validation, setValidation] = useState<ConditionValidation | null>(null);

  const evaluate = useMutation({
    mutationFn: () =>
      api<ConditionEvaluationResult>(`/v1/projects/${projectId}/conditions/evaluate`, {
        method: "POST",
        body: { customer_id: customerId, condition },
      }),
  });

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Try a rule on this customer</CardTitle>
          <p className="mt-1 text-xs text-muted-foreground">
            The same condition language guardrails, the lifecycle, workflows and feature flags are
            written in. An unknown fact counts as not matching, and the trace says which one.
          </p>
        </CardHeader>
        <CardContent className="space-y-3">
          <ConditionEditor
            projectId={projectId}
            value={condition}
            onChange={setCondition}
            onValidated={setValidation}
          />
          {catalog.data && !condition && (
            <div className="flex flex-wrap gap-1">
              {catalog.data.examples.map((example) => (
                <button
                  key={example}
                  type="button"
                  onClick={() => setCondition(example)}
                  className="border border-border bg-surface-2 px-2 py-0.5 font-mono text-[10px] hover:border-accent hover:text-accent"
                >
                  {example}
                </button>
              ))}
            </div>
          )}
          <Button
            size="sm"
            disabled={!condition.trim() || validation?.valid === false}
            loading={evaluate.isPending}
            onClick={() => evaluate.mutate()}
          >
            Evaluate
          </Button>
          {evaluate.error && <ErrorState error={evaluate.error} />}
          {evaluate.data && (
            <div className="space-y-2 border-t border-border pt-3">
              <p className="text-[12px]">
                {evaluate.data.evaluation.matched ? "Matches — " : "Does not match — "}
                <span className="text-muted-foreground">
                  {evaluate.data.evaluation.explanation}
                </span>
              </p>
              <ConditionTrace evaluation={evaluate.data.evaluation} />
              {evaluate.data.withheld_facts.length > 0 && (
                <p className="label">
                  Withheld by the restriction policy: {evaluate.data.withheld_facts.join(", ")}
                </p>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Facts</CardTitle>
          <p className="mt-1 text-xs text-muted-foreground">
            {facts.data
              ? `Computed ${formatRelative(facts.data.computed_at)} from this customer's memories, goals, health and forecast.`
              : "Computed from this customer's memories, goals, health and forecast."}
          </p>
        </CardHeader>
        <CardContent>
          {facts.isLoading && <LoadingRow />}
          {facts.error && <ErrorState error={facts.error} />}
          {facts.data && (
            <FactsTable facts={facts.data.values} withheld={facts.data.withheld_facts} />
          )}
        </CardContent>
      </Card>
    </div>
  );
}
