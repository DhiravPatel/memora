"use client";

import { useState } from "react";

import { ConditionEditor } from "@/components/condition-editor";
import { StateBadge } from "@/components/lifecycle";
import { Button } from "@/components/ui/button";
import { Input, Label, Select } from "@/components/ui/input";
import { useSession } from "@/hooks/use-session";
import { cn } from "@/lib/utils";
import type { LifecycleDefinition, LifecycleTransition } from "@/lib/types";

/** The lifecycle machine as a form: states, where customers start, and ordered rules.
 *
 * Order is the precedence — the first transition whose condition holds wins — so the list
 * is reorderable and the position is shown, rather than leaving precedence to be inferred.
 * Every `when` is checked against the fact catalog as it is typed; the server checks again
 * on save and refuses the whole machine if any rule does not compile.
 */
export function LifecycleEditor({
  value,
  onChange,
}: {
  value: LifecycleDefinition & { enabled?: boolean };
  onChange: (value: LifecycleDefinition & { enabled?: boolean }) => void;
}) {
  const { projectId } = useSession();
  const [newState, setNewState] = useState("");
  const enabled = value.enabled !== false;
  const states = value.states ?? [];
  const transitions = value.transitions ?? [];

  function update(patch: Partial<LifecycleDefinition & { enabled?: boolean }>) {
    onChange({ ...value, ...patch });
  }

  function setTransition(index: number, patch: Partial<LifecycleTransition>) {
    update({
      transitions: transitions.map((item, position) =>
        position === index ? { ...item, ...patch } : item,
      ),
    });
  }

  function move(index: number, delta: number) {
    const next = [...transitions];
    const target = index + delta;
    if (target < 0 || target >= next.length) return;
    [next[index], next[target]] = [next[target], next[index]];
    update({ transitions: next });
  }

  function addState() {
    const clean = newState.trim().toLowerCase().replace(/\s+/g, "_");
    if (!clean || states.includes(clean) || !/^[a-z0-9_]+$/.test(clean)) return;
    update({ states: [...states, clean] });
    setNewState("");
  }

  function removeState(state: string) {
    update({
      states: states.filter((item) => item !== state),
      initial:
        value.initial === state ? (states.find((item) => item !== state) ?? "") : value.initial,
      transitions: transitions.filter((item) => item.to !== state),
    });
  }

  return (
    <div className="w-full max-w-4xl space-y-5">
      <div className="flex items-center gap-3">
        <span className="label">Tracking</span>
        <div className="flex border border-border-strong">
          {[true, false].map((option) => (
            <button
              key={String(option)}
              onClick={() => update({ enabled: option })}
              className={cn(
                "px-3 py-1.5 font-mono text-[10px] uppercase tracking-label",
                enabled === option
                  ? "bg-accent text-accent-foreground"
                  : "bg-surface text-muted-foreground",
              )}
            >
              {option ? "on" : "off"}
            </button>
          ))}
        </div>
      </div>

      {enabled && (
        <>
          <div>
            <p className="label mb-2">States</p>
            <div className="flex flex-wrap items-center gap-2">
              {states.map((state) => (
                <span key={state} className="flex items-center gap-1">
                  <StateBadge state={state} />
                  <button
                    className="label hover:text-danger"
                    onClick={() => removeState(state)}
                    title="Remove state"
                  >
                    ✕
                  </button>
                </span>
              ))}
              <Input
                value={newState}
                onChange={(event) => setNewState(event.target.value)}
                onKeyDown={(event) => event.key === "Enter" && addState()}
                placeholder="new_state"
                className="h-7 w-32 text-[11px]"
              />
              <Button size="sm" variant="outline" onClick={addState}>
                Add
              </Button>
            </div>
          </div>

          <div className="w-56">
            <Label htmlFor="lifecycle-initial">New customers start in</Label>
            <Select
              id="lifecycle-initial"
              value={value.initial}
              onChange={(event) => update({ initial: event.target.value })}
            >
              {states.map((state) => (
                <option key={state} value={state}>
                  {state.replace(/_/g, " ")}
                </option>
              ))}
            </Select>
          </div>

          <div className="space-y-2">
            <p className="label">
              Transitions — tried in order; the first whose condition holds wins
            </p>
            {transitions.map((transition, index) => (
              <div
                key={`${transition.name}-${index}`}
                className="space-y-2 border border-border bg-surface-2 p-3"
              >
                <div className="flex flex-wrap items-end gap-2">
                  <span className="numeric w-6 text-xs text-muted-foreground">{index + 1}</span>
                  <div>
                    <Label>Name</Label>
                    <Input
                      value={transition.name}
                      onChange={(event) => setTransition(index, { name: event.target.value })}
                      className="h-8 w-36 text-[11px]"
                    />
                  </div>
                  <div className="min-w-[14rem] flex-1">
                    <Label>From</Label>
                    <div className="flex flex-wrap gap-1">
                      {["*", ...states].map((state) => {
                        const active = transition.from.includes(state);
                        return (
                          <button
                            key={state}
                            type="button"
                            onClick={() => {
                              const from =
                                state === "*"
                                  ? ["*"]
                                  : active
                                    ? transition.from.filter((item) => item !== state)
                                    : [...transition.from.filter((item) => item !== "*"), state];
                              setTransition(index, { from: from.length ? from : ["*"] });
                            }}
                            className={cn(
                              "border px-1.5 py-0.5 font-mono text-[10px]",
                              active
                                ? "border-accent bg-accent/10 text-accent"
                                : "border-border text-muted-foreground",
                            )}
                          >
                            {state === "*" ? "any" : state.replace(/_/g, " ")}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                  <div>
                    <Label>To</Label>
                    <Select
                      value={transition.to}
                      onChange={(event) => setTransition(index, { to: event.target.value })}
                      className="h-8 w-36"
                    >
                      {states.map((state) => (
                        <option key={state} value={state}>
                          {state.replace(/_/g, " ")}
                        </option>
                      ))}
                    </Select>
                  </div>
                  <div className="flex gap-1">
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => move(index, -1)}
                      disabled={index === 0}
                      title="Move up"
                    >
                      ↑
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => move(index, 1)}
                      disabled={index === transitions.length - 1}
                      title="Move down"
                    >
                      ↓
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() =>
                        update({
                          transitions: transitions.filter((_, position) => position !== index),
                        })
                      }
                      title="Remove transition"
                    >
                      ✕
                    </Button>
                  </div>
                </div>
                <ConditionEditor
                  projectId={projectId}
                  value={transition.when}
                  onChange={(when) => setTransition(index, { when })}
                />
              </div>
            ))}
            <Button
              size="sm"
              variant="outline"
              onClick={() =>
                update({
                  transitions: [
                    ...transitions,
                    {
                      name: `rule_${transitions.length + 1}`,
                      from: ["*"],
                      to: states[0] ?? "",
                      when: "",
                    },
                  ],
                })
              }
            >
              Add transition
            </Button>
          </div>
        </>
      )}
    </div>
  );
}
