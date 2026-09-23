"use client";

import { MemoryTypeBadge, StatusBadge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/states";
import { formatDate } from "@/lib/format";
import type { TimelineEntry } from "@/lib/types";

export function Timeline({ entries }: { entries: TimelineEntry[] }) {
  if (!entries.length) {
    return <EmptyState title="Nothing here yet" description="Send an event to start the timeline." />;
  }

  return (
    <ol className="relative space-y-0 border-l border-dashed border-border pl-6">
      {entries.map((entry) => (
        <li key={`${entry.kind}-${entry.id}`} className="relative py-3">
          <span
            className={`absolute -left-[29px] top-[18px] h-2.5 w-2.5 border ${
              entry.kind === "memory"
                ? "border-accent bg-accent"
                : "border-border-strong bg-surface"
            }`}
          />
          <div className="flex flex-wrap items-center gap-2">
            {entry.kind === "memory" ? (
              <MemoryTypeBadge type={entry.title} />
            ) : (
              <span className="font-mono text-[11px] uppercase tracking-label text-foreground">
                {entry.title}
              </span>
            )}
            <span className="label">{formatDate(entry.occurred_at)}</span>
            {typeof entry.metadata.status === "string" && (
              <StatusBadge status={entry.metadata.status} />
            )}
          </div>
          {entry.detail && (
            <p className="mt-1.5 text-sm leading-relaxed text-muted-foreground">{entry.detail}</p>
          )}
        </li>
      ))}
    </ol>
  );
}
