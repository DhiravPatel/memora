"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import type {
  ConditionEvaluation,
  ConditionLeaf,
  ConditionValidation,
  FactCatalog,
} from "@/lib/types";

/** The fact catalog, cached for the session: it only changes when the engine does. */
export function useFactCatalog(projectId: string | null) {
  return useQuery({
    queryKey: ["fact-catalog", projectId],
    queryFn: () => api<FactCatalog>(`/v1/projects/${projectId}/conditions/catalog`),
    enabled: Boolean(projectId),
    staleTime: 60 * 60 * 1000,
  });
}

/** A condition input that checks itself as you type.
 *
 * Shared by every feature written in the condition language — lifecycle transitions now,
 * guardrails, workflows and flags later — so a rule is validated the same way wherever it
 * is written, and the error points at the character that caused it rather than at the
 * whole field.
 */
export function ConditionEditor({
  projectId,
  value,
  onChange,
  onValidated,
  rows = 2,
  placeholder = 'health.score < 60 and problems.entities contains "shopify"',
}: {
  projectId: string | null;
  value: string;
  onChange: (value: string) => void;
  onValidated?: (validation: ConditionValidation | null) => void;
  rows?: number;
  placeholder?: string;
}) {
  const [validation, setValidation] = useState<ConditionValidation | null>(null);
  const [focused, setFocused] = useState(false);
  const catalog = useFactCatalog(projectId);
  const latest = useRef(value);
  const report = useRef(onValidated);
  report.current = onValidated;

  useEffect(() => {
    latest.current = value;
    if (!projectId || !value.trim()) {
      setValidation(null);
      report.current?.(null);
      return;
    }
    // Debounced: validating on every keystroke would race and flicker.
    const timer = setTimeout(async () => {
      try {
        const result = await api<ConditionValidation>(
          `/v1/projects/${projectId}/conditions/validate`,
          { method: "POST", body: { condition: value } },
        );
        if (latest.current === value) {
          setValidation(result);
          report.current?.(result);
        }
      } catch {
        // A network failure is not a verdict on the condition; say nothing.
      }
    }, 300);
    return () => clearTimeout(timer);
  }, [projectId, value]);

  // The fact being typed, for the hint list: the last dotted word before the cursor.
  const partial = useMemo(() => {
    const match = /([a-z_][a-z0-9_.]*)$/i.exec(value);
    return match ? match[1].toLowerCase() : "";
  }, [value]);
  const suggestions = useMemo(() => {
    if (!catalog.data || partial.length < 2) return [];
    return catalog.data.facts
      .filter((fact) => fact.name.startsWith(partial) && fact.name !== partial)
      .slice(0, 6);
  }, [catalog.data, partial]);

  function complete(name: string) {
    onChange(value.slice(0, value.length - partial.length) + name + " ");
  }

  const error = validation && !validation.valid ? validation : null;

  return (
    <div className="space-y-1.5">
      <textarea
        rows={rows}
        spellCheck={false}
        value={value}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
        onFocus={() => setFocused(true)}
        onBlur={() => setTimeout(() => setFocused(false), 150)}
        className={cn(
          "w-full border bg-surface px-3 py-2 font-mono text-[11px] leading-relaxed focus:outline-none",
          error
            ? "border-danger"
            : validation?.valid
              ? "border-success/60"
              : "border-border focus:border-accent",
        )}
      />

      {focused && suggestions.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {suggestions.map((fact) => (
            <button
              key={fact.name}
              type="button"
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => complete(fact.name)}
              title={fact.description}
              className="border border-border bg-surface-2 px-2 py-0.5 font-mono text-[10px] hover:border-accent hover:text-accent"
            >
              {fact.name} <span className="text-muted-foreground">{fact.type}</span>
            </button>
          ))}
        </div>
      )}

      {error && (
        <div className="border-l-2 border-danger bg-surface-2 px-3 py-1.5">
          {error.position !== null && (
            <pre className="overflow-x-auto font-mono text-[10px] leading-relaxed text-muted-foreground">
              {value}
              {"\n"}
              <span className="text-danger">{" ".repeat(Math.max(0, error.position))}^</span>
            </pre>
          )}
          <p className="font-mono text-[11px] text-danger">{error.error}</p>
        </div>
      )}
      {validation?.valid && validation.text !== value.trim() && (
        <p className="label">
          reads as <code className="font-mono normal-case text-foreground">{validation.text}</code>
        </p>
      )}
    </div>
  );
}

const OUTCOME_STYLES: Record<string, string> = {
  true: "border-success/70 bg-success/10 text-success",
  false: "border-border bg-surface-2 text-muted-foreground",
  unknown: "border-warning/70 bg-warning/10 text-warning",
};

/** An evaluation, leaf by leaf, with the ones that decided it marked. */
export function ConditionTrace({ evaluation }: { evaluation: Partial<ConditionEvaluation> }) {
  const leaves = evaluation.leaves ?? [];
  const decisive = new Set((evaluation.decisive ?? []).map((leaf) => leafKey(leaf)));
  return (
    <div className="space-y-2">
      {evaluation.outcome && (
        <div className="flex flex-wrap items-center gap-2">
          <Badge className={OUTCOME_STYLES[evaluation.outcome]}>{evaluation.outcome}</Badge>
          {evaluation.outcome === "unknown" && (
            <span className="text-[11px] text-muted-foreground">
              A fact it reads has no value yet, so it does not match.
            </span>
          )}
        </div>
      )}
      <ul className="space-y-px">
        {leaves.map((leaf, index) => (
          <li
            key={`${leafKey(leaf)}-${index}`}
            className={cn(
              "flex items-start gap-2 bg-surface-2 px-3 py-1.5",
              decisive.has(leafKey(leaf)) && "border-l-2 border-accent",
            )}
          >
            <Badge className={OUTCOME_STYLES[leaf.outcome]}>{leaf.outcome}</Badge>
            <div className="min-w-0 flex-1">
              <p className="font-mono text-[11px] leading-snug">{leaf.description}</p>
              {leaf.evidence.length > 0 && (
                <p className="label mt-0.5">
                  evidence: {leaf.evidence.slice(0, 4).join(", ")}
                  {leaf.evidence.length > 4 && ` +${leaf.evidence.length - 4}`}
                </p>
              )}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function leafKey(leaf: Pick<ConditionLeaf, "fact" | "op" | "expected">): string {
  return `${leaf.fact}|${leaf.op}|${JSON.stringify(leaf.expected)}`;
}
