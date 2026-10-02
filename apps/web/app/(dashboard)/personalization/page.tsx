"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { EmptyState, ErrorState, LoadingRow, PageHeader } from "@/components/ui/states";
import { useSession } from "@/hooks/use-session";
import { api } from "@/lib/api";
import { formatRelative } from "@/lib/format";
import type { PersonalizationRules, PersonalizationSummary } from "@/lib/types";

function words(value: string): string {
  return value.replace(/_/g, " ");
}

/** Share bars: one row per value, its count, and its share of the customers. */
function Shares({
  rows,
  total,
  mono = false,
}: {
  rows: [string, number][];
  total: number;
  mono?: boolean;
}) {
  if (rows.length === 0) {
    return <p className="px-5 py-4 text-xs text-muted-foreground">Nothing yet.</p>;
  }
  return (
    <ul>
      {rows.map(([name, count]) => {
        const share = total ? Math.round((count / total) * 100) : 0;
        return (
          <li key={name} className="space-y-1 border-t border-border px-5 py-2">
            <div className="flex items-baseline justify-between gap-3">
              <span
                className={
                  mono ? "font-mono text-[12px] text-foreground" : "text-sm text-foreground"
                }
              >
                {mono ? name : words(name)}
              </span>
              <span className="label">
                {count} · {share}%
              </span>
            </div>
            <div className="h-1.5 bg-surface-2">
              <div className="h-1.5 bg-accent" style={{ width: `${share}%` }} />
            </div>
          </li>
        );
      })}
    </ul>
  );
}

export default function PersonalizationPage() {
  const { projectId } = useSession();
  const [lookup, setLookup] = useState("");
  const summary = useQuery({
    queryKey: ["personalization-summary", projectId],
    queryFn: () => api<PersonalizationSummary>(`/v1/projects/${projectId}/personalization/summary`),
    enabled: Boolean(projectId),
  });
  const rules = useQuery({
    queryKey: ["personalization-rules", projectId],
    queryFn: () => api<PersonalizationRules>(`/v1/projects/${projectId}/personalization/rules`),
    enabled: Boolean(projectId),
  });
  const body = summary.data;
  const total = body?.customers ?? 0;
  const descriptions = Object.fromEntries(
    (rules.data?.hints ?? []).map((hint) => [hint.key, hint]),
  );

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Signal"
        title="Personalization"
        description="What your product is told about each customer — experience, mood, frictions, features and UI hints — computed as their events arrive. Read it with GET /v1/customers/{id}/personalization."
        actions={
          <div className="flex items-center gap-2">
            <Input
              value={lookup}
              onChange={(event) => setLookup(event.target.value)}
              placeholder="customer id"
              className="h-8 w-44 text-[12px]"
            />
            <Link href={lookup.trim() ? `/customers/${encodeURIComponent(lookup.trim())}` : "#"}>
              <Button size="sm" variant="outline" disabled={!lookup.trim()}>
                Open
              </Button>
            </Link>
            <Link href="/settings">
              <Button size="sm" variant="outline">
                Edit rules
              </Button>
            </Link>
          </div>
        }
      />
      {summary.isLoading && <LoadingRow />}
      {summary.error && <ErrorState error={summary.error} />}
      {body && total === 0 && (
        <EmptyState
          title="Nothing computed yet"
          description="Personalization is computed when a customer's events are processed, and nightly."
        />
      )}
      {body && total > 0 && (
        <>
          <Card>
            <CardHeader>
              <CardTitle>UI hints</CardTitle>
              <p className="mt-1 text-xs text-muted-foreground">
                How many of {total} customers each hint is on for
                {body.oldest_computed_at
                  ? ` · oldest computed ${formatRelative(body.oldest_computed_at)}`
                  : ""}
                .
              </p>
            </CardHeader>
            <ul>
              {Object.entries(body.hints)
                .sort((a, b) => b[1] - a[1])
                .map(([key, count]) => {
                  const share = Math.round((count / total) * 100);
                  return (
                    <li key={key} className="space-y-1 border-t border-border px-5 py-2.5">
                      <div className="flex flex-wrap items-baseline justify-between gap-3">
                        <div className="flex items-center gap-2">
                          <span className="font-mono text-[12px] text-foreground">{key}</span>
                          {descriptions[key]?.custom && <Badge>yours</Badge>}
                        </div>
                        <span className="label">
                          {count} · {share}%
                        </span>
                      </div>
                      {descriptions[key]?.description && (
                        <p className="text-xs text-muted-foreground">
                          {descriptions[key].description}
                        </p>
                      )}
                      <div className="h-1.5 bg-surface-2">
                        <div className="h-1.5 bg-accent" style={{ width: `${share}%` }} />
                      </div>
                    </li>
                  );
                })}
            </ul>
          </Card>
          <div className="grid gap-6 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>Experience</CardTitle>
              </CardHeader>
              <Shares rows={Object.entries(body.experience)} total={total} />
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>Mood</CardTitle>
              </CardHeader>
              <Shares rows={Object.entries(body.mood)} total={total} />
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>Known frictions</CardTitle>
              </CardHeader>
              <Shares
                rows={body.frictions.map((item) => [item.key, item.customers])}
                total={total}
                mono
              />
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>Relied-on features</CardTitle>
              </CardHeader>
              <Shares
                rows={body.relied_on_features.map((item) => [item.key, item.customers])}
                total={total}
                mono
              />
            </Card>
          </div>
          <Card>
            <CardContent>
              <p className="text-xs text-muted-foreground">
                Products read one customer with{" "}
                <span className="font-mono">GET /v1/customers/{"{id}"}/personalization</span> (send
                the ETag back as <span className="font-mono">If-None-Match</span> for a 304), many
                with <span className="font-mono">POST /v1/personalization/batch</span>, and hear
                about changes from the{" "}
                <span className="font-mono">customer.personalization_changed</span> webhook. A key
                with only <span className="font-mono">personalization:read</span> can do all three
                and read nothing else.
              </p>
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
