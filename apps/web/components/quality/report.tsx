"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, StatTile } from "@/components/ui/card";
import { ErrorState } from "@/components/ui/states";
import { Table, TD, TH, THead, TR } from "@/components/ui/table";
import { api } from "@/lib/api";
import { formatNumber } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { ProjectSettings, QualityDiagnostic, QualityReport } from "@/lib/types";

const SEVERITY: Record<string, string> = {
  critical: "border-danger/70 bg-danger/10 text-danger",
  warning: "border-warning/70 bg-warning/10 text-warning",
  info: "border-info/70 bg-info/10 text-info",
};

const pct = (value: number | null | undefined) =>
  value === null || value === undefined ? "—" : `${Math.round(value * 100)}%`;

/** The score, its parts, what is wrong and how to fix it, then the numbers underneath. */
export function QualityReportView({
  projectId,
  report,
  onOpenEvaluation,
  onOpenFreshness,
}: {
  projectId: string | null;
  report: QualityReport;
  onOpenEvaluation: (runId?: string) => void;
  onOpenFreshness?: () => void;
}) {
  const { events, consolidation, memories, searches } = report.metrics;
  return (
    <div className="space-y-6">
      <div className="grid gap-px border border-border bg-border sm:grid-cols-2 lg:grid-cols-7">
        <StatTile
          label="Quality score"
          value={report.score === null ? "—" : String(report.score)}
          tone="accent"
        />
        {report.components.map((component) => (
          <StatTile
            key={component.key}
            label={component.label}
            value={component.score === null ? "—" : String(component.score)}
          />
        ))}
      </div>
      <p className="label">
        The score is the mean of the parts that could be scored, over the last {report.window_days}{" "}
        days. A part with nothing to measure yet shows — and is left out, rather than counted as
        zero.
      </p>

      <Card>
        <CardHeader>
          <CardTitle>What to fix</CardTitle>
          <p className="mt-1 text-xs text-muted-foreground">
            Each finding names its cause and a concrete change, derived from the stored decision
            behind every event, your memories, the questions people asked and the last evaluation
            run.
          </p>
        </CardHeader>
        <CardContent className="space-y-2">
          {report.diagnostics.length === 0 && (
            <p className="text-[12px] text-success">Nothing to fix. Memory is in good shape.</p>
          )}
          {report.diagnostics.map((diagnostic, index) => (
            <DiagnosticRow
              key={`${diagnostic.key}-${index}`}
              projectId={projectId}
              diagnostic={diagnostic}
              onOpenEvaluation={onOpenEvaluation}
              onOpenFreshness={onOpenFreshness}
            />
          ))}
        </CardContent>
      </Card>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Events by type</CardTitle>
          </CardHeader>
          <Table>
            <THead>
              <TR>
                <TH>Type</TH>
                <TH className="text-right">Events</TH>
                <TH className="text-right">Became memory</TH>
                <TH className="text-right">Below threshold</TH>
                <TH className="text-right">Unreadable</TH>
              </TR>
            </THead>
            <tbody>
              {events.by_type.map((row) => (
                <TR key={row.event_type}>
                  <TD className="font-mono text-[11px]">{row.event_type}</TD>
                  <TD className="numeric text-right text-[11px]">{formatNumber(row.total)}</TD>
                  <TD className="numeric text-right text-[11px]">{formatNumber(row.produced)}</TD>
                  <TD
                    className={cn(
                      "numeric text-right text-[11px]",
                      row.below_threshold && "text-warning",
                    )}
                  >
                    {formatNumber(row.below_threshold)}
                  </TD>
                  <TD
                    className={cn("numeric text-right text-[11px]", row.no_text && "text-danger")}
                  >
                    {formatNumber(row.no_text)}
                  </TD>
                </TR>
              ))}
            </tbody>
          </Table>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Memory</CardTitle>
          </CardHeader>
          <CardContent>
            <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-[12px]">
              <Stat label="Active memories" value={formatNumber(memories.active)} />
              <Stat label="Average confidence" value={pct(memories.avg_confidence)} />
              <Stat label="Low confidence" value={formatNumber(memories.low_confidence)} />
              <Stat label="Not seen in 90 days" value={formatNumber(memories.stale)} />
              <Stat label="Expiring this week" value={formatNumber(memories.expiring_soon)} />
              <Stat label="Restricted" value={formatNumber(memories.restricted)} />
              <Stat label="Statements merged" value={formatNumber(consolidation.merges)} />
              <Stat label="Contradictions" value={formatNumber(consolidation.conflicts)} />
              <Stat
                label="Near-duplicate creates"
                value={`${formatNumber(consolidation.near_miss_creates)} (${pct(consolidation.near_miss_rate)})`}
              />
              <Stat
                label="Questions in unknown words"
                value={`${formatNumber(searches.with_unknown_terms)} of ${formatNumber(searches.total)}`}
              />
            </dl>
            {searches.unknown_terms.length > 0 && (
              <div className="mt-3">
                <p className="label mb-1">Asked about, never written down</p>
                <div className="flex flex-wrap gap-1">
                  {searches.unknown_terms.map((term) => (
                    <Badge key={term}>{term}</Badge>
                  ))}
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="label">{label}</dt>
      <dd className="numeric font-semibold">{value}</dd>
    </div>
  );
}

function DiagnosticRow({
  projectId,
  diagnostic,
  onOpenEvaluation,
  onOpenFreshness,
}: {
  projectId: string | null;
  diagnostic: QualityDiagnostic;
  onOpenEvaluation: (runId?: string) => void;
  onOpenFreshness?: () => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="bg-surface-2 px-3 py-2.5">
      <div className="flex flex-wrap items-start gap-3">
        <Badge className={SEVERITY[diagnostic.severity]}>{diagnostic.severity}</Badge>
        <div className="min-w-0 flex-1">
          <p className="text-[13px] font-semibold">{diagnostic.title}</p>
          <p className="mt-1 text-[12px] leading-relaxed text-muted-foreground">
            {diagnostic.detail}
          </p>
          {diagnostic.examples.length > 0 && (
            <button className="label mt-1.5 hover:text-accent" onClick={() => setOpen(!open)}>
              {open
                ? "hide examples"
                : `${diagnostic.examples.length} example${diagnostic.examples.length === 1 ? "" : "s"}`}
            </button>
          )}
          {open && (
            <pre className="mt-2 max-h-64 overflow-auto border border-border bg-surface p-2 font-mono text-[10px] leading-relaxed text-muted-foreground">
              {JSON.stringify(diagnostic.examples, null, 2)}
            </pre>
          )}
        </div>
        <FixAction
          projectId={projectId}
          fix={diagnostic.fix}
          onOpenEvaluation={onOpenEvaluation}
          onOpenFreshness={onOpenFreshness}
        />
      </div>
    </div>
  );
}

/** A fix, as the one action it implies — never a silent change: applying says what it sets. */
function FixAction({
  projectId,
  fix,
  onOpenEvaluation,
  onOpenFreshness,
}: {
  projectId: string | null;
  fix: Record<string, any>;
  onOpenEvaluation: (runId?: string) => void;
  onOpenFreshness?: () => void;
}) {
  const queryClient = useQueryClient();
  const apply = useMutation({
    mutationFn: async () => {
      const current = await api<ProjectSettings>(`/v1/projects/${projectId}/settings`);
      const [key, sub] = String(fix.setting).split(".", 2);
      const value = sub ? { ...(current.values[key] ?? {}), [sub]: fix.value } : fix.value;
      return api(`/v1/projects/${projectId}/settings`, {
        method: "PUT",
        body: { settings: { [key]: value } },
      });
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["quality"] }),
  });
  const retry = useMutation({
    mutationFn: () => api(`/v1/projects/${projectId}/events/retry-failed`, { method: "POST" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["quality"] }),
  });

  switch (fix.action) {
    case "set":
      return (
        <div className="text-right">
          <Button
            size="sm"
            variant="outline"
            loading={apply.isPending}
            onClick={() => apply.mutate()}
            title={`Sets ${fix.setting} to ${fix.value}`}
          >
            Set {fix.setting} = {fix.value}
          </Button>
          {apply.isSuccess && <p className="label mt-1 text-success">applied</p>}
          {apply.error && <ErrorState error={apply.error} />}
        </div>
      );
    case "retry_failed":
      return (
        <Button
          size="sm"
          variant="outline"
          loading={retry.isPending}
          onClick={() => retry.mutate()}
        >
          Retry failed events
        </Button>
      );
    case "create_evaluation":
      return (
        <Button size="sm" variant="outline" onClick={() => onOpenEvaluation()}>
          Create an evaluation
        </Button>
      );
    case "open_evaluation":
      return (
        <Button size="sm" variant="outline" onClick={() => onOpenEvaluation(fix.run_id)}>
          Open the run
        </Button>
      );
    case "add_vocabulary":
      return (
        <Link
          href="/settings"
          className="label border border-border px-2.5 py-1.5 hover:border-accent hover:text-accent"
        >
          Add to vocabulary →
        </Link>
      );
    case "review":
      if (fix.where === "drift" && onOpenFreshness) {
        return (
          <Button size="sm" variant="outline" onClick={onOpenFreshness}>
            Review drift
          </Button>
        );
      }
      return (
        <Link
          href={String(fix.where ?? "").startsWith("settings") ? "/settings" : "/memories"}
          className="label border border-border px-2.5 py-1.5 hover:border-accent hover:text-accent"
        >
          {String(fix.where ?? "").startsWith("settings") ? "Open settings →" : "Review memories →"}
        </Link>
      );
    case "preview":
      return (
        <Link
          href="/events"
          className="label border border-border px-2.5 py-1.5 hover:border-accent hover:text-accent"
        >
          Dry-run one →
        </Link>
      );
    default:
      return null;
  }
}
