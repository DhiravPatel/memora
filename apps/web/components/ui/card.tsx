import { cn } from "@/lib/utils";

export function Card({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("panel", className)} {...props} />;
}

export function CardHeader({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("rule-dashed px-5 py-4", className)} {...props} />;
}

export function CardTitle({ className, ...props }: React.HTMLAttributes<HTMLHeadingElement>) {
  return (
    <h3
      className={cn(
        "font-mono text-[11px] font-medium uppercase tracking-label text-foreground",
        className,
      )}
      {...props}
    />
  );
}

export function CardDescription({
  className,
  ...props
}: React.HTMLAttributes<HTMLParagraphElement>) {
  return (
    <p className={cn("mt-1.5 text-xs leading-relaxed text-muted-foreground", className)} {...props} />
  );
}

export function CardContent({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("px-5 py-4.5", className)} {...props} />;
}

/** Big number tile: mono label, oversized numeral, optional accent rail.
 *
 * A stat tile, not a chart: one number, its name, and at most one line of context. The
 * rail is the only colour, and only when the number means something.
 */
export function StatTile({
  label,
  value,
  tone,
  hint,
}: {
  label: string;
  value: string | number;
  tone?: "default" | "danger" | "accent";
  hint?: string;
}) {
  return (
    <div className="panel group relative overflow-hidden px-4 py-4 transition-shadow duration-200 ease-swift hover:shadow-md">
      <span
        className={cn(
          "absolute left-0 top-0 h-full w-[2px] transition-colors",
          tone === "danger"
            ? "bg-danger"
            : tone === "accent"
              ? "bg-accent"
              : "bg-border group-hover:bg-border-strong",
        )}
      />
      <p className="label">{label}</p>
      <p
        className={cn(
          "figure mt-2",
          tone === "danger" && "text-danger",
          tone === "accent" && "text-accent",
        )}
      >
        {value}
      </p>
      {hint && <p className="label mt-2">{hint}</p>}
    </div>
  );
}
