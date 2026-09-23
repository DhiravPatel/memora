"use client";

import { forwardRef } from "react";
import { cn } from "@/lib/utils";

type Variant = "primary" | "secondary" | "ghost" | "danger" | "outline";
type Size = "sm" | "md";

/* Primary and danger keep a 2px hard underside, so the one thing you click looks
   pressable; everything else is a quiet surface that lifts on hover. */
const VARIANTS: Record<Variant, string> = {
  primary:
    "bg-accent text-accent-foreground border-accent shadow-press hover:brightness-110 active:translate-y-[2px] active:shadow-none",
  secondary:
    "bg-surface text-foreground border-border shadow-sm hover:border-border-strong hover:bg-surface-2 active:translate-y-px",
  outline:
    "bg-transparent text-foreground border-border hover:border-border-strong hover:bg-surface-2",
  ghost:
    "bg-transparent text-muted-foreground border-transparent hover:text-foreground hover:bg-surface-2",
  danger:
    "bg-danger text-accent-foreground border-danger shadow-press hover:brightness-110 active:translate-y-[2px] active:shadow-none",
};

const SIZES: Record<Size, string> = {
  sm: "h-7 rounded-sm px-2.5 text-[10px]",
  md: "h-9 rounded-md px-4 text-[11px]",
};

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { className, variant = "primary", size = "md", loading, children, disabled, ...props },
  ref,
) {
  return (
    <button
      ref={ref}
      disabled={disabled || loading}
      className={cn(
        "inline-flex items-center justify-center gap-2 border font-mono font-medium uppercase tracking-label",
        "transition-[transform,box-shadow,background-color,border-color,filter] duration-150 ease-swift",
        "disabled:cursor-not-allowed disabled:opacity-40 disabled:shadow-none disabled:translate-y-0",
        VARIANTS[variant],
        SIZES[size],
        className,
      )}
      {...props}
    >
      {loading && (
        <span className="h-3 w-3 animate-spin border-2 border-current border-t-transparent" />
      )}
      {children}
    </button>
  );
});
