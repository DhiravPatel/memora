"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { RestrictionRule } from "@/lib/types";
import { cn } from "@/lib/utils";

/** A labelled row: control on the right, explanation underneath. */
export function Field({
  label,
  help,
  hint,
  children,
  changed,
}: {
  label: string;
  help?: string;
  hint?: React.ReactNode;
  children: React.ReactNode;
  changed?: boolean;
}) {
  return (
    <div className={cn("border-l-2 px-4 py-3.5", changed ? "border-accent" : "border-transparent")}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <span className="label-strong">{label}</span>
          {changed && <span className="label text-accent">modified</span>}
        </div>
        <div className="flex items-center gap-3">{children}</div>
      </div>
      {help && (
        <p className="mt-1.5 max-w-3xl text-[11px] leading-relaxed text-muted-foreground">{help}</p>
      )}
      {hint && <div className="mt-2">{hint}</div>}
    </div>
  );
}

/** Slider + numeric readout. Used for every 0-1 tunable. */
export function SliderControl({
  value,
  onChange,
  min = 0,
  max = 1,
  step = 0.01,
  format = (candidate: number) => candidate.toFixed(2),
}: {
  value: number;
  onChange: (value: number) => void;
  min?: number;
  max?: number;
  step?: number;
  format?: (value: number) => string;
}) {
  return (
    <div className="flex items-center gap-3">
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
        className="h-1 w-48 cursor-pointer appearance-none rounded-full bg-surface-3 accent-[hsl(var(--accent))]"
      />
      <span className="numeric w-12 text-right text-xs font-bold">{format(value)}</span>
    </div>
  );
}

export function NumberControl({
  value,
  onChange,
  min,
  max,
  step = 1,
  unit,
}: {
  value: number;
  onChange: (value: number) => void;
  min?: number;
  max?: number;
  step?: number;
  unit?: string | null;
}) {
  return (
    <div className="flex items-center gap-2">
      <Input
        type="number"
        className="h-8 w-32 text-right"
        value={Number.isFinite(value) ? value : 0}
        min={min}
        max={max}
        step={step}
        onChange={(event) => onChange(Number(event.target.value))}
      />
      {unit && <span className="label w-16">{unit}</span>}
    </div>
  );
}

export function ToggleControl({
  value,
  onChange,
  labels = ["on", "off"],
}: {
  value: boolean;
  onChange: (value: boolean) => void;
  labels?: [string, string];
}) {
  return (
    <div className="flex border border-border-strong">
      {[true, false].map((option, index) => (
        <button
          key={String(option)}
          onClick={() => onChange(option)}
          className={cn(
            "px-3 py-1.5 font-mono text-[10px] uppercase tracking-label transition-colors",
            value === option
              ? "bg-accent text-accent-foreground"
              : "bg-surface text-muted-foreground hover:bg-surface-2",
          )}
        >
          {labels[index]}
        </button>
      ))}
    </div>
  );
}

/** Editable key → number table, used for per-event-type importance and retention. */
export function MapEditor({
  entries,
  onChange,
  keyPlaceholder,
  min = 0,
  max = 1,
  step = 0.01,
  fixedKeys,
  unit,
}: {
  entries: Record<string, number>;
  onChange: (entries: Record<string, number>) => void;
  keyPlaceholder?: string;
  min?: number;
  max?: number;
  step?: number;
  fixedKeys?: string[];
  unit?: string | null;
}) {
  const keys = fixedKeys ?? Object.keys(entries);

  function setValue(key: string, value: number) {
    onChange({ ...entries, [key]: value });
  }

  function addRow(key: string) {
    const clean = key.trim().toLowerCase().replace(/\s+/g, "_");
    if (!clean || clean in entries) return;
    onChange({ ...entries, [clean]: min });
  }

  function removeRow(key: string) {
    const next = { ...entries };
    delete next[key];
    onChange(next);
  }

  return (
    <div className="w-full max-w-2xl">
      {keys.length === 0 && (
        <p className="mb-2 text-[11px] text-muted-foreground">
          No overrides — the built-in importance table applies.
        </p>
      )}
      <div className="space-y-px">
        {keys.map((key) => (
          <div key={key} className="flex items-center gap-2 bg-surface-2 px-2 py-1.5">
            <code className="flex-1 font-mono text-[11px]">{key.replace(/_/g, " ")}</code>
            <Input
              type="number"
              className="h-7 w-24 text-right"
              value={entries[key] ?? min}
              min={min}
              max={max}
              step={step}
              onChange={(event) => setValue(key, Number(event.target.value))}
            />
            {unit && <span className="label w-10">{unit}</span>}
            {!fixedKeys && (
              <Button size="sm" variant="ghost" onClick={() => removeRow(key)} title="Remove">
                ✕
              </Button>
            )}
          </div>
        ))}
      </div>
      {!fixedKeys && <AddRow placeholder={keyPlaceholder} onAdd={addRow} />}
    </div>
  );
}

function AddRow({ placeholder, onAdd }: { placeholder?: string; onAdd: (key: string) => void }) {
  return (
    <form
      className="mt-2 flex gap-2"
      onSubmit={(event) => {
        event.preventDefault();
        const input = event.currentTarget.elements.namedItem("key") as HTMLInputElement;
        onAdd(input.value);
        input.value = "";
      }}
    >
      <Input
        name="key"
        placeholder={placeholder ?? "event_type"}
        className="h-8 flex-1 uppercase tracking-label"
      />
      <Button size="sm" variant="outline" type="submit">
        Add
      </Button>
    </form>
  );
}

/** Ranking weights: sliders plus a live share bar, because only ratios matter. */
export function WeightsEditor({
  weights,
  onChange,
  keys,
}: {
  weights: Record<string, number>;
  onChange: (weights: Record<string, number>) => void;
  keys: string[];
}) {
  const total = keys.reduce((sum, key) => sum + (weights[key] ?? 0), 0) || 1;
  const COLORS = ["bg-accent", "bg-success", "bg-warning", "bg-info", "bg-violet"];

  return (
    <div className="w-full max-w-2xl space-y-2">
      <div className="flex h-2 w-full overflow-hidden border border-border">
        {keys.map((key, index) => (
          <div
            key={key}
            className={COLORS[index % COLORS.length]}
            style={{ width: `${((weights[key] ?? 0) / total) * 100}%` }}
            title={`${key} ${(((weights[key] ?? 0) / total) * 100).toFixed(0)}%`}
          />
        ))}
      </div>
      <div className="space-y-px">
        {keys.map((key) => (
          <div key={key} className="flex items-center gap-3 bg-surface-2 px-2 py-1.5">
            <span className="label w-28">{key}</span>
            <input
              type="range"
              min={0}
              max={1}
              step={0.01}
              value={weights[key] ?? 0}
              onChange={(event) => onChange({ ...weights, [key]: Number(event.target.value) })}
              className="h-1 flex-1 cursor-pointer appearance-none rounded-full bg-surface-3 accent-[hsl(var(--accent))]"
            />
            <span className="numeric w-10 text-right text-[11px]">
              {(weights[key] ?? 0).toFixed(2)}
            </span>
            <span className="numeric w-12 text-right text-[11px] text-muted-foreground">
              {(((weights[key] ?? 0) / total) * 100).toFixed(0)}%
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

/** Restriction rules: which memories only a cleared reader may see.
 *
 * Each rule is one row rather than a JSON blob, because the failure mode being designed
 * against is a rule that looks right and matches nothing. The row shows what the rule
 * matches on, and the pattern kind is validated as it is typed rather than on save.
 */
export function PolicyEditor({
  rules,
  onChange,
  memoryTypes,
}: {
  rules: RestrictionRule[];
  onChange: (rules: RestrictionRule[]) => void;
  memoryTypes: string[];
}) {
  const [kind, setKind] = useState<RestrictionRule["kind"]>("term");
  const [value, setValue] = useState("");
  const [label, setLabel] = useState("");

  const patternError = (() => {
    if (kind !== "pattern" || !value.trim()) return null;
    try {
      new RegExp(value);
      return null;
    } catch (error) {
      return (error as Error).message;
    }
  })();

  function add() {
    const trimmed = value.trim();
    if (!trimmed || patternError) return;
    onChange([...rules, { kind, value: trimmed, label: label.trim() }]);
    setValue("");
    setLabel("");
  }

  function remove(index: number) {
    onChange(rules.filter((_, position) => position !== index));
  }

  return (
    <div className="w-full max-w-2xl space-y-2">
      {rules.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">
          No restrictions — every memory is readable by any key with{" "}
          <code className="font-mono">memory:read</code>.
        </p>
      ) : (
        <div className="space-y-px">
          {rules.map((rule, index) => (
            <div
              key={`${rule.kind}-${rule.value}-${index}`}
              className="flex items-center gap-2 bg-surface-2 px-2 py-1.5"
            >
              <span className="label w-14 text-accent">{rule.kind}</span>
              <code className="flex-1 truncate font-mono text-[11px]" title={rule.value}>
                {rule.value}
              </code>
              {rule.label && (
                <span className="max-w-[12rem] truncate text-[11px] text-muted-foreground">
                  {rule.label}
                </span>
              )}
              <Button size="sm" variant="ghost" onClick={() => remove(index)} title="Remove">
                ✕
              </Button>
            </div>
          ))}
        </div>
      )}

      <div className="flex flex-wrap items-start gap-2 pt-1">
        <div className="flex border border-border-strong">
          {(["term", "type", "pattern"] as const).map((option) => (
            <button
              key={option}
              onClick={() => {
                setKind(option);
                setValue("");
              }}
              className={cn(
                "px-2.5 py-1.5 font-mono text-[10px] uppercase tracking-label transition-colors",
                kind === option
                  ? "bg-accent text-accent-foreground"
                  : "bg-surface text-muted-foreground hover:bg-surface-2",
              )}
            >
              {option}
            </button>
          ))}
        </div>

        {kind === "type" ? (
          <select
            value={value}
            onChange={(event) => setValue(event.target.value)}
            className="h-8 flex-1 min-w-[10rem] border border-border bg-surface px-2 font-mono text-[11px]"
          >
            <option value="">select a memory type…</option>
            {memoryTypes.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        ) : (
          <Input
            value={value}
            onChange={(event) => setValue(event.target.value)}
            placeholder={kind === "term" ? "salary, bonus, lawsuit" : "case\\s+no\\.?\\s*\\d+"}
            className="h-8 flex-1 min-w-[12rem] font-mono text-[11px]"
          />
        )}

        <Input
          value={label}
          onChange={(event) => setLabel(event.target.value)}
          placeholder="reason (optional)"
          className="h-8 w-40 text-[11px]"
        />
        <Button
          size="sm"
          variant="outline"
          onClick={add}
          disabled={!value.trim() || !!patternError}
        >
          Add
        </Button>
      </div>

      {patternError && (
        <p className="text-[11px] text-danger">Invalid expression: {patternError}</p>
      )}
      <p className="text-[11px] leading-relaxed text-muted-foreground">
        A <strong>term</strong> rule matches any memory mentioning one of those words, including
        plurals. A <strong>type</strong> rule restricts a whole class. A <strong>pattern</strong>{" "}
        rule matches a regular expression, case-insensitively. Changing these re-classifies existing
        memories in the background.
      </p>
    </div>
  );
}
