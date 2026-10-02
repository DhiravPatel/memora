"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState, ErrorState, LoadingRow } from "@/components/ui/states";
import { api } from "@/lib/api";
import { formatDate, formatRelative } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { Personalization } from "@/lib/types";

function words(value: string | null | undefined): string {
  return String(value ?? "—").replace(/_/g, " ");
}

const MOOD_STYLES: Record<string, string> = {
  frustrated: "border-danger/70 bg-danger/10 text-danger",
  happy: "border-success/70 bg-success/10 text-success",
};

/** What the product is told about this customer, and why (§26 6.6). */
export function PersonalizationPanel({
  projectId,
  customerId,
}: {
  projectId: string | null;
  customerId: string;
}) {
  const queryClient = useQueryClient();
  const [copied, setCopied] = useState(false);
  const base = `/v1/projects/${projectId}/customers/${encodeURIComponent(customerId)}/personalization`;
  const personalization = useQuery({
    queryKey: ["customer-personalization", projectId, customerId],
    queryFn: () => api<Personalization>(base),
    enabled: Boolean(projectId),
  });
  const refresh = useMutation({
    mutationFn: () => api<Personalization>(`${base}/refresh`, { method: "POST" }),
    onSuccess: (data) =>
      queryClient.setQueryData(["customer-personalization", projectId, customerId], data),
  });

  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 2000);
    return () => clearTimeout(timer);
  }, [copied]);

  const body = personalization.data;
  const details = body?.details;
  const payload = body
    ? JSON.stringify(
        Object.fromEntries(Object.entries(body).filter(([key]) => key !== "details")),
        null,
        2,
      )
    : "";

  async function copy() {
    await navigator.clipboard.writeText(payload);
    setCopied(true);
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <CardTitle>Personalization</CardTitle>
              <p className="mt-1 text-xs text-muted-foreground">
                What <span className="font-mono">GET /v1/customers/{"{id}"}/personalization</span>{" "}
                tells your product — each value with the facts behind it. Recomputed whenever their
                events are processed; restricted memories never reach it.
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button
                size="sm"
                variant="outline"
                loading={refresh.isPending}
                onClick={() => refresh.mutate()}
              >
                Recompute
              </Button>
              <Button size="sm" variant="outline" onClick={copy} disabled={!body}>
                {copied ? "Copied" : "Copy JSON"}
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          {personalization.isLoading && <LoadingRow />}
          {personalization.error && <ErrorState error={personalization.error} />}
          {refresh.error && <ErrorState error={refresh.error} />}
          {body && (
            <p className="label">
              computed {formatRelative(body.computed_at)} · last changed{" "}
              <span title={formatDate(body.changed_at)}>{formatRelative(body.changed_at)}</span> ·
              version {body.version}
            </p>
          )}
        </CardContent>
      </Card>

      {body && details && (
        <div className="grid gap-6 lg:grid-cols-[1.15fr_1fr]">
          <div className="space-y-6">
            <Card>
              <CardHeader>
                <CardTitle>UI hints</CardTitle>
                <p className="mt-1 text-xs text-muted-foreground">
                  Conditions over the customer&apos;s facts, their experience and mood, and what the
                  guardrails would say — change them in Settings → Personalization.
                </p>
              </CardHeader>
              <ul>
                {Object.entries(details.ui).map(([key, hint]) => (
                  <li key={key} className="space-y-1 border-t border-border px-5 py-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-mono text-[12px] text-foreground">{key}</span>
                      <Badge
                        className={
                          hint.value ? "border-accent/70 bg-accent/10 text-accent" : undefined
                        }
                      >
                        {hint.value ? "on" : "off"}
                      </Badge>
                      {!hint.known && (
                        <Badge title="A fact the hint reads is unknown for this customer">
                          unknown
                        </Badge>
                      )}
                      {hint.custom && <Badge>yours</Badge>}
                    </div>
                    {hint.because.length > 0 && (
                      <p className="text-xs text-foreground">{hint.because.join("; ")}</p>
                    )}
                    <p className="font-mono text-[10px] text-muted-foreground">{hint.rule}</p>
                  </li>
                ))}
              </ul>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Known frictions</CardTitle>
              </CardHeader>
              {details.known_frictions.length === 0 ? (
                <EmptyState title="Nothing in their way" description="No open problems." />
              ) : (
                <ul>
                  {details.known_frictions.map((friction) => (
                    <li
                      key={friction.key}
                      className="flex flex-wrap items-baseline gap-2 border-t border-border px-5 py-2.5"
                    >
                      <span className="font-mono text-[12px] text-foreground">{friction.key}</span>
                      <span className="text-xs text-muted-foreground">{friction.label}</span>
                      <Badge
                        className={
                          friction.mode === "broken"
                            ? "border-danger/70 bg-danger/10 text-danger"
                            : friction.mode === "slow"
                              ? "border-warning/70 bg-warning/10 text-warning"
                              : undefined
                        }
                      >
                        {friction.mode}
                      </Badge>
                      <span className="label ml-auto">
                        {friction.problems} problem{friction.problems === 1 ? "" : "s"} ·{" "}
                        {friction.reports} report{friction.reports === 1 ? "" : "s"} · since{" "}
                        {formatDate(friction.since)}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Features used</CardTitle>
              </CardHeader>
              {details.features_used.length === 0 ? (
                <EmptyState
                  title="No features recorded"
                  description="Feature and integration events name what they use."
                />
              ) : (
                <ul>
                  {details.features_used.map((feature) => (
                    <li
                      key={feature.key}
                      className="flex flex-wrap items-baseline gap-2 border-t border-border px-5 py-2.5"
                    >
                      <span className="text-sm text-foreground">{feature.name}</span>
                      <span className="font-mono text-[10px] text-muted-foreground">
                        {feature.key}
                      </span>
                      <Badge>{feature.kind}</Badge>
                      {feature.relied_on && (
                        <Badge className="border-success/70 bg-success/10 text-success">
                          relied on
                        </Badge>
                      )}
                      <span className="label ml-auto">
                        {feature.uses} uses · {feature.recent_uses} recent · last{" "}
                        {formatRelative(feature.last_used_at)}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          </div>

          <div className="space-y-6">
            <Card>
              <CardHeader>
                <CardTitle>The customer, for the product</CardTitle>
              </CardHeader>
              <dl className="grid grid-cols-[8.5rem_1fr] text-sm">
                <Row label="Experience">
                  <span className="text-foreground">{words(details.experience.value)}</span>
                  <Why reasons={details.experience.because} rule={details.experience.rule} />
                </Row>
                <Row label="Mood">
                  <Badge className={cn(MOOD_STYLES[details.mood.value ?? ""] ?? "")}>
                    {words(details.mood.value)}
                  </Badge>
                  <Why reasons={details.mood.because} rule={details.mood.rule} />
                </Row>
                <Row label="Channel">
                  <span className="text-foreground">{words(details.preferred_channel.value)}</span>
                  {details.preferred_channel.outdated && (
                    <p className="text-xs text-warning">
                      possibly outdated — their contacts say{" "}
                      {words(details.preferred_channel.observed)}
                    </p>
                  )}
                  {details.preferred_channel.opt_outs.length > 0 && (
                    <p className="text-xs text-muted-foreground">
                      opted out: {details.preferred_channel.opt_outs.join(", ")}
                    </p>
                  )}
                </Row>
                <Row label="Current goal">
                  {details.current_goal ? (
                    <>
                      <span className="text-foreground">{details.current_goal.label}</span>
                      <p className="text-xs text-muted-foreground">
                        <span className="font-mono">{details.current_goal.key}</span> ·{" "}
                        {details.current_goal.status} ·{" "}
                        {Math.round(details.current_goal.progress * 100)}%
                      </p>
                    </>
                  ) : (
                    <span className="text-muted-foreground">none</span>
                  )}
                </Row>
                <Row label="Stage">
                  {Object.entries(details.stage).length === 0 && (
                    <span className="text-muted-foreground">not tracked</span>
                  )}
                  {Object.entries(details.stage).map(([track, state]) => (
                    <p key={track} className="text-xs">
                      <span className="label">{track}</span>{" "}
                      <span className="text-foreground">{words(state)}</span>
                    </p>
                  ))}
                </Row>
                <Row label="Plan · health">
                  <span className="text-foreground">
                    {words(body.plan)} · {words(details.health.band)}
                    {details.health.score != null ? ` (${Math.round(details.health.score)})` : ""}
                  </span>
                </Row>
              </dl>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>What the product receives</CardTitle>
              </CardHeader>
              <pre className="max-h-[28rem] overflow-auto border-t border-border bg-surface-2 px-5 py-3 font-mono text-[11px] leading-relaxed text-foreground">
                {payload}
              </pre>
            </Card>
          </div>
        </div>
      )}
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <>
      <dt className="label border-t border-border px-5 py-2.5">{label}</dt>
      <dd className="space-y-0.5 border-t border-border px-5 py-2.5">{children}</dd>
    </>
  );
}

function Why({ reasons, rule }: { reasons: string[]; rule: string | null }) {
  if (reasons.length === 0 && !rule) {
    return <p className="text-xs text-muted-foreground">no other rule applied</p>;
  }
  return (
    <>
      {reasons.length > 0 && <p className="text-xs text-muted-foreground">{reasons.join("; ")}</p>}
      {rule && <p className="font-mono text-[10px] text-muted-foreground">{rule}</p>}
    </>
  );
}
