"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { FreshnessOverview } from "@/components/freshness";
import { EvaluationPanel } from "@/components/quality/evaluation";
import { QualityReportView } from "@/components/quality/report";
import { Select } from "@/components/ui/input";
import { ErrorState, LoadingRow, PageHeader } from "@/components/ui/states";
import { useSession } from "@/hooks/use-session";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { QualityReport } from "@/lib/types";

const TABS = ["Report", "Freshness", "Evaluation"] as const;

export default function QualityPage() {
  const { projectId } = useSession();
  const [tab, setTab] = useState<(typeof TABS)[number]>("Report");
  const [days, setDays] = useState(30);
  const [focusRun, setFocusRun] = useState<string | null>(null);

  const report = useQuery({
    queryKey: ["quality", projectId, days],
    queryFn: () => api<QualityReport>(`/v1/projects/${projectId}/quality`, { query: { days } }),
    enabled: Boolean(projectId) && (tab === "Report" || tab === "Freshness"),
  });

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Pipeline"
        title="Memory quality"
        description="How good this project's memory is — and for every part that is not, why, and what to change."
        actions={
          tab === "Report" ? (
            <Select
              value={days}
              onChange={(event) => setDays(Number(event.target.value))}
              className="w-36"
            >
              {[7, 30, 90].map((value) => (
                <option key={value} value={value}>
                  Last {value} days
                </option>
              ))}
            </Select>
          ) : null
        }
      />

      <div className="flex gap-px overflow-hidden rounded-md border border-border bg-border">
        {TABS.map((item) => (
          <button
            key={item}
            onClick={() => setTab(item)}
            className={cn(
              "flex-1 px-4 py-2.5 font-mono text-[11px] uppercase tracking-label",
              tab === item
                ? "bg-accent text-accent-foreground"
                : "bg-surface text-muted-foreground hover:bg-surface-2",
            )}
          >
            {item}
          </button>
        ))}
      </div>

      {tab === "Report" && (
        <>
          {report.isLoading && <LoadingRow />}
          {report.error && <ErrorState error={report.error} />}
          {report.data && (
            <QualityReportView
              projectId={projectId}
              report={report.data}
              onOpenEvaluation={(runId) => {
                setFocusRun(runId ?? null);
                setTab("Evaluation");
              }}
              onOpenFreshness={() => setTab("Freshness")}
            />
          )}
        </>
      )}
      {tab === "Freshness" && (
        <FreshnessOverview projectId={projectId} memories={report.data?.metrics?.memories} />
      )}
      {tab === "Evaluation" && <EvaluationPanel projectId={projectId} focusRun={focusRun} />}
    </div>
  );
}
