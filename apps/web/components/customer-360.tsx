"use client";

import { useState } from "react";

import { Badge, MemoryTypeBadge, RestrictedBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatRelative } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { Customer360 } from "@/lib/types";

/** What an agent sees before it replies.
 *
 * Deliberately shows the *response*, not a prettier version of it. The other tabs on this
 * page already present each section better than this can; the reason to look here is to
 * see what one call actually hands an agent — including its size, which is the constraint
 * nobody thinks about until a prompt stops fitting.
 */
export function Customer360View({ view }: { view: Customer360 }) {
  const [raw, setRaw] = useState(false);
  const bytes = new TextEncoder().encode(JSON.stringify(view)).length;
  const sections = Object.entries(view.sections);

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="flex flex-row items-start justify-between gap-4">
          <div>
            <CardTitle>What an agent gets</CardTitle>
            <p className="mt-1 text-xs text-muted-foreground">{view.summary}</p>
          </div>
          <Button size="sm" variant={raw ? "primary" : "outline"} onClick={() => setRaw(!raw)}>
            {raw ? "Sections" : "Raw JSON"}
          </Button>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap gap-x-6 gap-y-1">
            <Figure label="Sections" value={String(sections.length)} />
            <Figure label="Payload" value={`${(bytes / 1024).toFixed(1)} kB`} />
            <Figure
              label="Rough tokens"
              // Four characters to a token is the usual rule of thumb. Approximate on
              // purpose — the useful signal is the order of magnitude, not the number.
              value={`~${Math.round(bytes / 4).toLocaleString()}`}
            />
            {view.withheld > 0 && <Figure label="Withheld" value={String(view.withheld)} danger />}
          </div>
        </CardContent>
      </Card>

      {raw ? (
        <Card>
          <CardContent>
            <pre className="max-h-[32rem] overflow-auto font-mono text-[10px] leading-relaxed text-muted-foreground">
              {JSON.stringify(view, null, 2)}
            </pre>
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          {sections.map(([name, value]) => (
            <Card key={name}>
              <CardHeader>
                <CardTitle className="text-sm">{name.replace(/_/g, " ")}</CardTitle>
              </CardHeader>
              <CardContent>
                <SectionBody name={name} value={value} />
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}

function Figure({ label, value, danger }: { label: string; value: string; danger?: boolean }) {
  return (
    <div>
      <p className="label">{label}</p>
      <p className={cn("numeric text-xs font-semibold", danger && "text-danger")}>{value}</p>
    </div>
  );
}

function SectionBody({ name, value }: { name: string; value: any }) {
  if (value === null || (Array.isArray(value) && value.length === 0)) {
    return <p className="label">Nothing recorded</p>;
  }

  if (name === "health") {
    return (
      <div className="space-y-2">
        <div className="flex items-baseline gap-2">
          <span className="display-lg">{Math.round(value.score)}</span>
          <Badge>{value.band}</Badge>
        </div>
        <p className="text-[11px] leading-relaxed text-muted-foreground">{value.explanation}</p>
      </div>
    );
  }

  if (name === "risk_signals") {
    return (
      <div className="space-y-2">
        <div className="flex flex-wrap gap-x-6 gap-y-1">
          <Figure label="Trajectory" value={value.trajectory} />
          <Figure label="Churn risk" value={value.churn_risk.toFixed(2)} />
          <Figure label="Confidence" value={value.confidence.toFixed(2)} />
        </div>
        <ul className="space-y-1">
          {(value.observations ?? []).slice(0, 4).map((signal: any) => (
            <li key={signal.key} className="text-[11px] text-muted-foreground">
              {signal.label} — {signal.rationale}
            </li>
          ))}
        </ul>
      </div>
    );
  }

  if (name === "subscription") {
    return <MemoryLine memory={value} />;
  }

  if (Array.isArray(value)) {
    return (
      <ul className="space-y-1.5">
        {value.slice(0, 6).map((item: any, index: number) => (
          <li key={item.id ?? index}>
            {item.content ? (
              <MemoryLine memory={item} />
            ) : (
              <p className="text-[11px] leading-snug">
                {item.action ?? item.statement ?? item.summary ?? item.entity ?? item.type}
                {item.rationale && (
                  <span className="text-muted-foreground"> — {item.rationale}</span>
                )}
                {item.occurred_at && (
                  <span className="label ml-1">{formatRelative(item.occurred_at)}</span>
                )}
              </p>
            )}
          </li>
        ))}
        {value.length > 6 && <li className="label">+{value.length - 6} more</li>}
      </ul>
    );
  }

  return (
    <pre className="overflow-auto font-mono text-[10px] text-muted-foreground">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

function MemoryLine({ memory }: { memory: any }) {
  return (
    <div>
      <p className="text-[11px] leading-snug">{memory.content}</p>
      <div className="mt-0.5 flex flex-wrap items-center gap-1.5">
        <MemoryTypeBadge type={memory.type} />
        {memory.sensitivity === "restricted" && <RestrictedBadge />}
        <span className="label">
          {memory.evidence_count}× · {formatRelative(memory.last_seen_at)}
        </span>
      </div>
    </div>
  );
}
