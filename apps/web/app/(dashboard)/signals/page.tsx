"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { SignalBar, TrajectoryBadge } from "@/components/foresight";
import { HealthMeter } from "@/components/health-card";
import { Card } from "@/components/ui/card";
import { Select } from "@/components/ui/input";
import { EmptyState, ErrorState, LoadingRow, PageHeader } from "@/components/ui/states";
import { Table, TD, TH, THead, TR } from "@/components/ui/table";
import { useSession } from "@/hooks/use-session";
import { api } from "@/lib/api";
import type { PortfolioSignals } from "@/lib/types";

const FILTERS = [
  { label: "Heading the wrong way", value: "declining" },
  { label: "Everyone", value: "" },
  { label: "Improving", value: "improving" },
];

export default function SignalsPage() {
  const { projectId } = useSession();
  const [trajectories, setTrajectories] = useState(FILTERS[0]!.value);

  const portfolio = useQuery({
    queryKey: ["portfolio-signals", projectId, trajectories],
    queryFn: () =>
      api<PortfolioSignals>(`/v1/projects/${projectId}/signals`, {
        query: { trajectories, limit: 100 },
      }),
    enabled: Boolean(projectId),
  });

  const counts = portfolio.data?.trajectories ?? {};

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Signal"
        title="Where customers are heading"
        description="Two windows of the same memory graph, compared against each other. Health says where they are; this says which way they are moving."
        actions={
          <Select
            value={trajectories}
            onChange={(event) => setTrajectories(event.target.value)}
            className="w-56"
          >
            {FILTERS.map((filter) => (
              <option key={filter.label} value={filter.value}>
                {filter.label}
              </option>
            ))}
          </Select>
        }
      />

      <div className="grid gap-px overflow-hidden rounded-lg border border-border bg-border sm:grid-cols-3">
        {(["declining", "steady", "improving"] as const).map((trajectory) => (
          <div key={trajectory} className="bg-surface px-4 py-3.5">
            <p className="label">{trajectory}</p>
            <p className="figure mt-2">
              {counts[trajectory] ?? 0}
            </p>
          </div>
        ))}
      </div>

      <Card>
        {portfolio.isLoading && <LoadingRow />}
        {portfolio.error && <ErrorState error={portfolio.error} />}
        {portfolio.data?.customers.length === 0 && (
          <EmptyState
            title="Nobody in this group"
            description="No customer's memory currently points this way."
          />
        )}
        {!!portfolio.data?.customers.length && (
          <Table>
            <THead>
              <TR>
                <TH className="w-56">Customer</TH>
                <TH className="w-32">Trajectory</TH>
                <TH className="w-28">Churn risk</TH>
                <TH className="w-44">Health</TH>
                <TH>Leading signal</TH>
              </TR>
            </THead>
            <tbody>
              {portfolio.data.customers.map((report) => {
                const leading = report.signals[0];
                return (
                  <TR key={report.customer_id}>
                    <TD>
                      <Link
                        href={`/customers/${report.customer_id}`}
                        className="text-sm font-semibold text-foreground hover:text-accent"
                      >
                        {report.name || report.external_id}
                      </Link>
                      <p className="label mt-0.5">{report.external_id}</p>
                    </TD>
                    <TD>
                      <TrajectoryBadge trajectory={report.trajectory} />
                    </TD>
                    <TD>
                      <span className="numeric text-sm font-bold">
                        {Math.round(report.churn_risk * 100)}%
                      </span>
                      <p className="label mt-0.5">
                        {Math.round(report.confidence * 100)}% confidence
                      </p>
                    </TD>
                    <TD>
                      <HealthMeter score={report.health_score} width={14} />
                    </TD>
                    <TD className="max-w-xl">
                      {leading ? (
                        <div className="flex items-start gap-3">
                          <SignalBar signal={leading} />
                          <p className="text-xs leading-relaxed text-muted-foreground">
                            {leading.rationale}
                          </p>
                        </div>
                      ) : (
                        <span className="label">nothing either way</span>
                      )}
                    </TD>
                  </TR>
                );
              })}
            </tbody>
          </Table>
        )}
      </Card>
    </div>
  );
}
