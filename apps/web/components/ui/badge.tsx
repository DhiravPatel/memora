import { cn, MEMORY_TYPE_COLORS, STATUS_COLORS } from "@/lib/utils";

export function Badge({ className, ...props }: React.HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-sm border px-2 py-0.5 font-mono text-[10px] uppercase tracking-label",
        "border-border bg-surface-2 text-muted-foreground",
        className,
      )}
      {...props}
    />
  );
}

export function MemoryTypeBadge({ type }: { type: string }) {
  return <Badge className={MEMORY_TYPE_COLORS[type] ?? ""}>{type}</Badge>;
}

export function StatusBadge({ status }: { status: string }) {
  return <Badge className={STATUS_COLORS[status] ?? ""}>{status}</Badge>;
}

/** Marks a memory the project's policy restricted, and says which rule did it.
 *
 * Only a reader with clearance ever receives one of these, so the badge is a reminder
 * that what they are reading is gated — not a warning that they should not be here.
 */
export function RestrictedBadge({ reason }: { reason?: string | null }) {
  return (
    <Badge
      className="border-danger/40 bg-danger/10 text-danger"
      title={reason ? `Restricted: ${reason}` : "Restricted by this project's policy"}
    >
      restricted
    </Badge>
  );
}

/** Segmented 0-1 meter. Reads as a gauge, not a progress bar.
 *
 * Ten ticks rather than a continuous bar, because a reader can count ticks and cannot
 * estimate a fill; and the value is printed beside it either way.
 */
export function ScoreBar({ value, label }: { value: number; label?: string }) {
  const clamped = Math.max(0, Math.min(1, value));
  const filled = Math.round(clamped * 10);
  return (
    <div className="flex items-center gap-2">
      <div className="flex gap-[2px]">
        {Array.from({ length: 10 }).map((_, index) => (
          <span
            key={index}
            className={cn(
              "h-3 w-[3px] rounded-[1px] transition-colors",
              index < filled
                ? clamped >= 0.66
                  ? "bg-success"
                  : clamped >= 0.33
                    ? "bg-warning"
                    : "bg-danger"
                : "bg-surface-3",
            )}
          />
        ))}
      </div>
      <span className="numeric text-[10px] text-muted-foreground">
        {label ?? clamped.toFixed(2)}
      </span>
    </div>
  );
}
