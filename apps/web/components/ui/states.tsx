import { cn } from "@/lib/utils";

export function Spinner({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        "inline-block h-3.5 w-3.5 animate-spin border-2 border-muted-foreground border-t-transparent",
        className,
      )}
    />
  );
}

export function LoadingRow({ label = "Loading" }: { label?: string }) {
  return (
    <div className="label flex items-center gap-2 px-5 py-10">
      <Spinner /> {label}…
    </div>
  );
}

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-center gap-2.5 px-5 py-16 text-center">
      <span className="font-display text-lg text-foreground">{title}</span>
      {description && (
        <p className="max-w-md text-xs leading-relaxed text-muted-foreground">{description}</p>
      )}
      {action}
    </div>
  );
}

export function ErrorState({ error }: { error: unknown }) {
  const message = error instanceof Error ? error.message : "Something went wrong.";
  return (
    <div className="m-5 rounded-md border border-danger/30 bg-danger/[0.06] px-4 py-3">
      <p className="label-strong text-danger">Error</p>
      <p className="mt-1 font-mono text-xs leading-relaxed text-danger">{message}</p>
    </div>
  );
}

/** Page header: mono eyebrow, editorial title, one line of context.
 *
 * The eyebrow carries a short accent rule before it — a small piece of furniture that
 * makes the page feel set rather than laid out.
 */
export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  actions?: React.ReactNode;
}) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-4 border-b border-border pb-5">
      <div>
        {eyebrow && (
          <p className="label mb-2.5 flex items-center gap-2">
            <span className="h-px w-5 bg-accent" aria-hidden />
            {eyebrow}
          </p>
        )}
        <h1 className="display-xl">{title}</h1>
        {description && (
          <p className="mt-3 max-w-2xl text-[13px] leading-relaxed text-muted-foreground">
            {description}
          </p>
        )}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}
