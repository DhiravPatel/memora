"use client";

import { MemoryTypeBadge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/states";
import { formatDate } from "@/lib/format";
import { LINK_TYPE_LABELS } from "@/lib/utils";
import type { CustomerLinks } from "@/lib/types";

export function CausalChains({ data }: { data: CustomerLinks }) {
  if (!data.chains.length && !data.links.length) {
    return (
      <Card>
        <EmptyState
          title="No links yet"
          description="Links appear once a customer has an outcome — a downgrade, a cancellation — with problems or feedback recorded before it."
        />
      </Card>
    );
  }

  return (
    <div className="space-y-5">
      {data.chains.map((chain) => (
        <Card key={chain.outcome_memory_id}>
          <CardHeader>
            <p className="label mb-1.5">Outcome · {formatDate(chain.occurred_at)}</p>
            <CardTitle className="font-display text-[17px] normal-case tracking-[-0.01em]">
              {chain.outcome_content}
            </CardTitle>
            <p className="label mt-1.5">
              {chain.steps.length} contributing {chain.steps.length === 1 ? "memory" : "memories"}
            </p>
          </CardHeader>
          <CardContent>
            <ol className="relative space-y-0 border-l border-dashed border-border pl-6">
              {chain.steps.map((step) => (
                <li key={step.memory_id} className="relative py-3">
                  <span className="absolute -left-[29px] top-[18px] h-2.5 w-2.5 border border-accent bg-accent" />
                  <div className="flex flex-wrap items-center gap-2">
                    <MemoryTypeBadge type={step.type} />
                    <span className="label">
                      {formatDate(step.occurred_at)} · {(step.confidence * 100).toFixed(0)}% confidence
                    </span>
                  </div>
                  <p className="mt-1.5 text-sm leading-relaxed">{step.content}</p>
                  {step.rationale && <p className="label mt-1">{step.rationale}</p>}
                </li>
              ))}
            </ol>
          </CardContent>
        </Card>
      ))}

      <Card>
        <CardHeader>
          <CardTitle>All inferred links</CardTitle>
        </CardHeader>
        <CardContent className="space-y-1.5">
          {data.links.map((link) => (
            <div
              key={link.id}
              className="flex flex-wrap items-center gap-2 border-b border-border/60 py-1.5 last:border-0"
            >
              <span className="border border-border bg-surface-2 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-label text-accent">
                {LINK_TYPE_LABELS[link.link_type] ?? link.link_type}
              </span>
              <span className="flex-1 text-xs text-muted-foreground">
                {link.other_content ?? link.other_memory_id}
              </span>
              <span className="numeric text-[10px] text-muted-foreground">
                {(link.confidence * 100).toFixed(0)}%
              </span>
            </div>
          ))}
        </CardContent>
      </Card>
    </div>
  );
}
