"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { ConditionEditor } from "@/components/condition-editor";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input, Label } from "@/components/ui/input";
import { ErrorState } from "@/components/ui/states";
import { useSession } from "@/hooks/use-session";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { Personalization, PersonalizationRules, PersonalizationSettings } from "@/lib/types";

type Ordered = { name: string; when: string | null }[];

/** Personalization rules as a form (§26 6.6): experience levels and moods tried in order,
 *  the built-in hints (switch off or rewrite), the project's own hints, and a preview of
 *  what the unsaved rules would tell the product about one customer.
 *
 * Only what differs from the built-ins is stored; the server compiles every condition again
 * on save and refuses the lot if any does not.
 */
export function PersonalizationEditor({
  value,
  onChange,
}: {
  value: PersonalizationSettings;
  onChange: (value: PersonalizationSettings) => void;
}) {
  const { projectId } = useSession();
  const rules = useQuery({
    queryKey: ["personalization-rules", projectId],
    queryFn: () => api<PersonalizationRules>(`/v1/projects/${projectId}/personalization/rules`),
    enabled: Boolean(projectId),
  });
  const defaults = rules.data?.defaults;
  const hints = value.hints ?? {};

  function update(patch: Partial<PersonalizationSettings>) {
    const next = { ...value, ...patch };
    for (const key of Object.keys(patch) as (keyof PersonalizationSettings)[]) {
      if (patch[key] === undefined) delete next[key];
    }
    onChange(next);
  }

  function setHint(
    key: string,
    patch: { when?: string; description?: string; enabled?: boolean } | null,
  ) {
    const next = { ...hints };
    if (patch === null) delete next[key];
    else next[key] = { ...(next[key] ?? {}), ...patch };
    update({ hints: Object.keys(next).length ? next : undefined });
  }

  const experience: Ordered | undefined = value.experience?.map((item) => ({
    name: item.level,
    when: item.when,
  }));
  const mood: Ordered | undefined = value.mood?.map((item) => ({
    name: item.mood,
    when: item.when,
  }));
  const custom = Object.keys(hints).filter((key) => !(defaults?.hints ?? {})[key]);

  return (
    <div className="w-full max-w-4xl space-y-6">
      <OrderedRules
        title="Experience levels"
        help="Tried in order; the first that holds is the level. The last may have no condition: it is the fallback."
        rules={experience}
        defaults={defaults?.experience.map((item) => ({ name: item.level, when: item.when })) ?? []}
        onChange={(next) =>
          update({ experience: next?.map((item) => ({ level: item.name, when: item.when })) })
        }
      />
      <OrderedRules
        title="Moods"
        help="How the customer seems right now — the first that holds."
        rules={mood}
        defaults={defaults?.mood.map((item) => ({ name: item.mood, when: item.when })) ?? []}
        onChange={(next) =>
          update({ mood: next?.map((item) => ({ mood: item.name, when: item.when })) })
        }
      />

      <div>
        <p className="label mb-2">Built-in hints</p>
        <div className="divide-y divide-border border border-border">
          {Object.entries(defaults?.hints ?? {}).map(([key, base]) => {
            const override = hints[key] ?? {};
            const on = override.enabled !== false;
            const rewritten = override.when !== undefined;
            return (
              <div key={key} className="space-y-2 px-3 py-2.5">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div>
                    <p className="font-mono text-[11px] text-foreground">{key}</p>
                    <p className="text-xs text-muted-foreground">
                      {override.description ?? base.description}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    {rewritten && (
                      <button
                        className="label hover:text-accent"
                        onClick={() => setHint(key, { when: undefined })}
                      >
                        restore rule
                      </button>
                    )}
                    <div className="flex border border-border-strong">
                      {[true, false].map((option) => (
                        <button
                          key={String(option)}
                          onClick={() => setHint(key, { enabled: option ? undefined : false })}
                          className={cn(
                            "px-3 py-1 font-mono text-[10px] uppercase tracking-label",
                            on === option
                              ? "bg-accent text-accent-foreground"
                              : "text-muted-foreground hover:text-foreground",
                          )}
                        >
                          {option ? "on" : "off"}
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
                {on && (
                  <ConditionEditor
                    projectId={projectId}
                    value={override.when ?? base.when}
                    onChange={(next) =>
                      setHint(key, { when: next === base.when ? undefined : next })
                    }
                  />
                )}
              </div>
            );
          })}
        </div>
      </div>

      <div>
        <div className="mb-2 flex items-center justify-between">
          <p className="label">Your hints</p>
          <Button
            size="sm"
            variant="outline"
            onClick={() => {
              let index = custom.length + 1;
              while (hints[`my_hint_${index}`]) index += 1;
              setHint(`my_hint_${index}`, { when: 'health.band == "healthy"', description: "" });
            }}
          >
            Add a hint
          </Button>
        </div>
        {custom.length === 0 && (
          <p className="text-xs text-muted-foreground">
            A hint is any condition over the customer&apos;s facts — e.g.{" "}
            <span className="font-mono">
              personalization.experience == &quot;advanced&quot; and plan == &quot;pro&quot;
            </span>{" "}
            to invite power users to a beta.
          </p>
        )}
        <div className="space-y-3">
          {custom.map((key) => (
            <CustomHint
              key={key}
              name={key}
              hint={hints[key] ?? {}}
              projectId={projectId}
              onRename={(next) => {
                if (!next || next === key || hints[next]) return;
                const copy = { ...hints, [next]: hints[key] };
                delete copy[key];
                update({ hints: copy });
              }}
              onChange={(patch) => setHint(key, patch)}
              onRemove={() => setHint(key, null)}
            />
          ))}
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <Label>Relied on: at least this many uses…</Label>
          <Input
            type="number"
            min={1}
            max={1000}
            value={value.relied_on_uses ?? defaults?.relied_on_uses ?? 3}
            onChange={(event) =>
              update({
                relied_on_uses:
                  Number(event.target.value) === defaults?.relied_on_uses
                    ? undefined
                    : Number(event.target.value),
              })
            }
          />
        </div>
        <div>
          <Label>…in this many days</Label>
          <Input
            type="number"
            min={1}
            max={365}
            value={value.relied_on_days ?? defaults?.relied_on_days ?? 30}
            onChange={(event) =>
              update({
                relied_on_days:
                  Number(event.target.value) === defaults?.relied_on_days
                    ? undefined
                    : Number(event.target.value),
              })
            }
          />
        </div>
      </div>

      <Preview projectId={projectId} rules={value} />
    </div>
  );
}

function OrderedRules({
  title,
  help,
  rules,
  defaults,
  onChange,
}: {
  title: string;
  help: string;
  rules: Ordered | undefined;
  defaults: Ordered;
  onChange: (next: Ordered | undefined) => void;
}) {
  const { projectId } = useSession();
  const shown = rules ?? defaults;
  const customised = rules !== undefined;

  function set(index: number, patch: Partial<Ordered[number]>) {
    onChange(shown.map((rule, position) => (position === index ? { ...rule, ...patch } : rule)));
  }

  function move(index: number, by: number) {
    const next = [...shown];
    const [rule] = next.splice(index, 1);
    next.splice(index + by, 0, rule);
    onChange(next);
  }

  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="label">{title}</p>
          <p className="text-xs text-muted-foreground">{help}</p>
        </div>
        {customised ? (
          <button className="label hover:text-accent" onClick={() => onChange(undefined)}>
            restore defaults
          </button>
        ) : (
          <Button
            size="sm"
            variant="outline"
            onClick={() => onChange(defaults.map((rule) => ({ ...rule })))}
          >
            Customize
          </Button>
        )}
      </div>
      <div className="divide-y divide-border border border-border">
        {shown.map((rule, index) => (
          <div key={`${index}-${rule.name}`} className="flex flex-wrap items-start gap-2 px-3 py-2">
            <Input
              value={rule.name}
              disabled={!customised}
              onChange={(event) => set(index, { name: event.target.value.toLowerCase() })}
              className="h-8 w-36 font-mono text-[11px]"
              aria-label={`${title} name`}
            />
            <div className="min-w-[16rem] flex-1">
              {customised ? (
                <ConditionEditor
                  projectId={projectId}
                  value={rule.when ?? ""}
                  onChange={(next) => set(index, { when: next.trim() ? next : null })}
                  rows={1}
                  placeholder={index === shown.length - 1 ? "(fallback — leave empty)" : undefined}
                />
              ) : (
                <p className="py-1.5 font-mono text-[11px] text-muted-foreground">
                  {rule.when ?? "otherwise"}
                </p>
              )}
            </div>
            {customised && (
              <div className="flex gap-1">
                <button
                  className="label px-1 disabled:opacity-30"
                  disabled={index === 0}
                  onClick={() => move(index, -1)}
                >
                  ↑
                </button>
                <button
                  className="label px-1 disabled:opacity-30"
                  disabled={index === shown.length - 1}
                  onClick={() => move(index, 1)}
                >
                  ↓
                </button>
                <button
                  className="label px-1 hover:text-danger disabled:opacity-30"
                  disabled={shown.length === 1}
                  onClick={() => onChange(shown.filter((_, position) => position !== index))}
                >
                  ×
                </button>
              </div>
            )}
          </div>
        ))}
      </div>
      {customised && (
        <button
          className="label mt-2 hover:text-accent"
          onClick={() =>
            onChange([...shown.slice(0, -1), { name: "", when: "" }, ...shown.slice(-1)])
          }
        >
          + add a rule before the fallback
        </button>
      )}
    </div>
  );
}

function CustomHint({
  name,
  hint,
  projectId,
  onRename,
  onChange,
  onRemove,
}: {
  name: string;
  hint: { when?: string; description?: string; enabled?: boolean };
  projectId: string | null;
  onRename: (next: string) => void;
  onChange: (patch: { when?: string; description?: string }) => void;
  onRemove: () => void;
}) {
  const [draftName, setDraftName] = useState(name);
  return (
    <div className="space-y-2 border border-border px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <Input
          value={draftName}
          onChange={(event) =>
            setDraftName(event.target.value.toLowerCase().replace(/[^a-z0-9_]/g, "_"))
          }
          onBlur={() => onRename(draftName)}
          className="h-8 w-56 font-mono text-[11px]"
          aria-label="Hint name"
        />
        <Input
          value={hint.description ?? ""}
          onChange={(event) => onChange({ description: event.target.value })}
          placeholder="What the product should do when it is on"
          className="h-8 min-w-[14rem] flex-1 text-[12px]"
        />
        <button className="label hover:text-danger" onClick={onRemove}>
          remove
        </button>
      </div>
      <ConditionEditor
        projectId={projectId}
        value={hint.when ?? ""}
        onChange={(next) => onChange({ when: next })}
      />
    </div>
  );
}

function Preview({
  projectId,
  rules,
}: {
  projectId: string | null;
  rules: PersonalizationSettings;
}) {
  const [customer, setCustomer] = useState("");
  const preview = useMutation({
    mutationFn: () =>
      api<Personalization>(`/v1/projects/${projectId}/personalization/preview`, {
        method: "POST",
        body: { customer_id: customer.trim(), rules },
      }),
  });
  const body = preview.data;
  return (
    <div className="space-y-3 border border-dashed border-border-strong px-3 py-3">
      <div className="flex flex-wrap items-end gap-2">
        <div className="min-w-[14rem] flex-1">
          <Label>Try these rules on a customer (nothing is saved)</Label>
          <Input
            value={customer}
            onChange={(event) => setCustomer(event.target.value)}
            placeholder="customer id, e.g. cus_123"
          />
        </div>
        <Button
          size="sm"
          onClick={() => preview.mutate()}
          disabled={!customer.trim()}
          loading={preview.isPending}
        >
          Preview
        </Button>
      </div>
      {preview.error && <ErrorState error={preview.error} />}
      {body && (
        <div className="space-y-2 text-xs">
          <p>
            <span className="label">experience</span> {body.experience ?? "—"}{" "}
            <span className="label ml-3">mood</span> {body.mood ?? "—"}
          </p>
          <div className="flex flex-wrap gap-1.5">
            {Object.entries(body.ui).map(([key, on]) => (
              <Badge
                key={key}
                className={on ? "border-accent/70 bg-accent/10 text-accent" : undefined}
                title={body.details?.ui[key]?.because.join("; ")}
              >
                {key}: {on ? "on" : "off"}
              </Badge>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
