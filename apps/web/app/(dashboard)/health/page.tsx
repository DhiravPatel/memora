"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { HealthBadge, HealthMeter } from "@/components/health-card";
import { Card } from "@/components/ui/card";
import { Select } from "@/components/ui/input";
import { EmptyState, ErrorState, LoadingRow, PageHeader } from "@/components/ui/states";
import { Table, TD, TH, THead, TR } from "@/components/ui/table";
import { useSession } from "@/hooks/use-session";
import { api } from "@/lib/api";
import type { PortfolioHealth } from "@/lib/types";

const FILTERS = [
  { label: "Needs attention", value: "at_risk,critical" },
  { label: "Watch and below", value: "watch,at_risk,critical" },
  { label: "Everyone", value: "healthy,watch,at_risk,critical" },
];

export default function HealthPage() {
  const { projectId } = useSession();
  const [bands, setBands] = useState(FILTERS[0]!.value);

  const portfolio = useQuery({
    queryKey: ["portfolio-health", projectId, bands],
    queryFn: () =>
      api<PortfolioHealth>(`/v1/projects/${projectId}/health`, { query: { bands, limit: 100 } }),
    enabled: Boolean(projectId),
  });

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Signal"
        title="Customer health"
        description="Scored from memory alone — open problems, churn language, feedback, engagement and silence."
        actions={
          <Select value={bands} onChange={(event) => setBands(event.target.value)} className="w-56">
            {FILTERS.map((filter) => (
              <option key={filter.value} value={filter.value}>
                {filter.label}
              </option>
            ))}
          </Select>
        }
      />

      <Card>
        {portfolio.isLoading && <LoadingRow />}
        {portfolio.error && <ErrorState error={portfolio.error} />}
        {portfolio.data?.customers.length === 0 && (
          <EmptyState
            title="Nobody in this band"
            description="No customers currently match the selected health bands."
          />
        )}
        {!!portfolio.data?.customers.length && (
          <Table>
            <THead>
              <TR>
                <TH className="w-56">Customer</TH>
                <TH className="w-52">Score</TH>
                <TH className="w-28">Band</TH>
                <TH>Why</TH>
              </TR>
            </THead>
            <tbody>
              {portfolio.data.customers.map((customer) => (
                <TR key={customer.customer_id}>
                  <TD>
                    <Link
                      href={`/customers/${customer.customer_id}`}
                      className="text-sm font-semibold text-foreground hover:text-accent"
                    >
                      {customer.name || customer.external_id}
                    </Link>
                    <p className="label mt-0.5">{customer.external_id}</p>
                  </TD>
                  <TD>
                    <HealthMeter score={customer.score} />
                  </TD>
                  <TD>
                    <HealthBadge band={customer.band} />
                  </TD>
                  <TD className="max-w-xl text-xs leading-relaxed text-muted-foreground">
                    {customer.explanation}
                  </TD>
                </TR>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </div>
  );
}
