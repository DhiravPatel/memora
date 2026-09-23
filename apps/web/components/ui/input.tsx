"use client";

import { forwardRef } from "react";
import { cn } from "@/lib/utils";

/* Inset rather than outlined: the field is a well in the card, and focus lights its edge
   plus a soft ring, which reads as modern without a coloured fill. */
const base =
  "w-full rounded-md border border-border bg-surface-2 px-3 py-2 font-mono text-xs text-foreground " +
  "outline-none transition-[border-color,box-shadow,background-color] duration-150 ease-swift " +
  "placeholder:text-faint hover:border-border-strong " +
  "focus:border-accent focus:bg-surface focus:shadow-[0_0_0_3px_hsl(var(--accent)/0.12)] " +
  "disabled:cursor-not-allowed disabled:opacity-50";

export const Input = forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  function Input({ className, ...props }, ref) {
    return <input ref={ref} className={cn(base, "h-9", className)} {...props} />;
  },
);

export const Textarea = forwardRef<
  HTMLTextAreaElement,
  React.TextareaHTMLAttributes<HTMLTextAreaElement>
>(function Textarea({ className, ...props }, ref) {
  return (
    <textarea ref={ref} className={cn(base, "min-h-[92px] resize-y leading-relaxed", className)} {...props} />
  );
});

export const Select = forwardRef<HTMLSelectElement, React.SelectHTMLAttributes<HTMLSelectElement>>(
  function Select({ className, ...props }, ref) {
    return (
      <select
        ref={ref}
        className={cn(base, "h-9 appearance-none pr-8 uppercase tracking-label", className)}
        style={{
          backgroundImage:
            "linear-gradient(45deg, transparent 50%, currentColor 50%), linear-gradient(135deg, currentColor 50%, transparent 50%)",
          backgroundPosition: "calc(100% - 16px) 50%, calc(100% - 11px) 50%",
          backgroundSize: "5px 5px, 5px 5px",
          backgroundRepeat: "no-repeat",
        }}
        {...props}
      />
    );
  },
);

export function Label({ className, ...props }: React.LabelHTMLAttributes<HTMLLabelElement>) {
  return <label className={cn("label mb-1.5 block", className)} {...props} />;
}
