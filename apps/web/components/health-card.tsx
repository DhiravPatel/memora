"use client";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn, HEALTH_BAND_COLORS } from "@/lib/utils";
import type { Health } from "@/lib/types";

export function HealthBadge({ band }: { band: string }) {
  return <Badge className={HEALTH_BAND_COLORS[band] ?? ""}>{band.replace("_", " ")}</Badge>;
}

/** 20-segment gauge: brutalist, unambiguous, readable at a glance in a table row. */
export function HealthMeter({ score, width = 20 }: { score: number; width?: number }) {
  const clamped = Math.max(0, Math.min(100, score));
  const filled = Math.round((clamped / 100) * width);
  const tone =
    clamped >= 80
      ? "bg-success"
      : clamped >= 60
        ? "bg-warning"
        : clamped >= 35
          ? "bg-danger"
          : "bg-critical";

  return (
    <div className="flex items-center gap-2">
      <div className="flex gap-[2px]">
        {Array.from({ length: width }).map((_, index) => (
          <span
            key={index}
            className={cn(
              "h-3.5 w-[3px] rounded-[1px] transition-colors",
              index < filled ? tone : "bg-surface-3",
            )}
          />
        ))}
      </div>
      <span className="numeric text-[11px] font-bold">{Math.round(clamped)}</span>
    </div>
  );
}

export function HealthCard({ health }: { health: Health }) {
  const negatives = health.factors.filter((factor) => factor.contribution < 0);
  const positives = health.factors.filter((factor) => factor.contribution > 0);

  return (
    <Card>
      <CardHeader className="flex flex-wrap items-center justify-between gap-3">
        <CardTitle>Health</CardTitle>
        <div className="flex items-center gap-3">
          <HealthMeter score={health.score} />
          <HealthBadge band={health.band} />
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm leading-relaxed">{health.explanation}</p>
        <div className="grid gap-px border border-border bg-border sm:grid-cols-2">
          <FactorList title="Pulling down" factors={negatives} tone="danger" />
          <FactorList title="Holding up" factors={positives} tone="accent" />
        </div>
        <p className="label">
          {health.memories_considered} memories · {health.events_considered} events · churn risk{" "}
          {(health.churn_risk * 100).toFixed(0)}%
        </p>
      </CardContent>
    </Card>
  );
}

function FactorList({
  title,
  factors,
  tone,
}: {
  title: string;
  factors: Health["factors"];
  tone: "danger" | "accent";
}) {
  return (
    <div className="bg-surface p-4">
      <p className="label mb-2.5">{title}</p>
      {!factors.length && <p className="text-xs text-muted-foreground">Nothing recorded.</p>}
      <ul className="space-y-1.5">
        {factors.map((factor) => (
          <li key={factor.key} className="flex items-baseline justify-between gap-3 text-xs">
            <span className="text-muted-foreground">{factor.label}</span>
            <span className={cn("numeric font-bold", tone === "danger" ? "text-danger" : "text-success")}>
              {factor.contribution > 0 ? "+" : ""}
              {factor.contribution.toFixed(1)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
