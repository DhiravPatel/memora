"use client";

import { useGuardrailCatalog } from "@/components/agents/shared";
import { ConditionEditor } from "@/components/condition-editor";
import { Button } from "@/components/ui/button";
import { Input, Label, Select } from "@/components/ui/input";
import { useSession } from "@/hooks/use-session";
import { cn } from "@/lib/utils";
import type { AutoApprovalLimit, GuardrailRule, GuardrailSettings } from "@/lib/types";

/** Agent guardrails as a form: the built-in rules, automatic approval limits, the project's
 *  own rules, and the approval window.
 *
 * Project rules are conditions over customer facts plus the proposed action's `request.*`
 * facts, checked against the catalog as they are typed; the server compiles every one again
 * on save and refuses the lot if any does not.
 */
export function GuardrailsEditor({
  value,
  builtins,
  onChange,
}: {
  value: GuardrailSettings;
  builtins: string[];
  onChange: (value: GuardrailSettings) => void;
}) {
  const { projectId } = useSession();
  const catalog = useGuardrailCatalog(projectId);
  const disabled = new Set(value.disabled ?? []);
  const rules = value.rules ?? [];
  const describe = (rule: string) =>
    catalog.data?.builtin_rules.find((item) => item.rule === rule)?.description;

  function update(patch: Partial<GuardrailSettings>) {
    onChange({ ...value, ...patch });
  }

  function setLimit(index: number, patch: Partial<AutoApprovalLimit>) {
    update({
      auto_approve: (value.auto_approve ?? []).map((limit, position) =>
        position === index ? { ...limit, ...patch } : limit,
      ),
    });
  }

  function setRule(index: number, patch: Partial<GuardrailRule>) {
    update({
      rules: rules.map((rule, position) => (position === index ? { ...rule, ...patch } : rule)),
    });
  }

  return (
    <div className="w-full max-w-4xl space-y-5">
      <div>
        <p className="label mb-2">Built-in rules</p>
        <div className="divide-y divide-border border border-border">
          {builtins.map((rule) => {
            const on = !disabled.has(rule);
            return (
              <div key={rule} className="flex items-center justify-between gap-3 px-3 py-2">
                <div>
                  <p className="font-mono text-[11px] text-foreground">{rule}</p>
                  {describe(rule) && (
                    <p className="text-xs text-muted-foreground">{describe(rule)}</p>
                  )}
                </div>
                <div className="flex border border-border-strong">
                  {[true, false].map((option) => (
                    <button
                      key={String(option)}
                      onClick={() =>
                        update({
                          disabled: option
                            ? (value.disabled ?? []).filter((item) => item !== rule)
                            : [...(value.disabled ?? []), rule],
                        })
                      }
                      className={cn(
                        "px-3 py-1 font-mono text-[10px] uppercase tracking-label",
                        on === option
                          ? "bg-accent text-accent-foreground"
                          : "bg-surface text-muted-foreground",
                      )}
                    >
                      {option ? "on" : "off"}
                    </button>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      <div className="space-y-2">
        <p className="label">
          Automatic approval — money and account actions within these limits need nobody
        </p>
        {(value.auto_approve ?? []).map((limit, index) => (
          <div
            key={index}
            className="flex flex-wrap items-end gap-2 border border-border bg-surface-2 p-3"
          >
            <div className="min-w-[14rem] flex-1">
              <Label>Actions (comma-separated)</Label>
              <Input
                list="guardrail-limit-actions"
                value={limit.actions.join(", ")}
                placeholder="process_refund"
                onChange={(event) =>
                  setLimit(index, {
                    actions: event.target.value
                      .split(",")
                      .map((item) => item.trim())
                      .filter(Boolean),
                  })
                }
                className="h-8 text-[11px]"
              />
            </div>
            <div>
              <Label>Up to amount</Label>
              <Input
                type="number"
                min={0}
                value={limit.up_to ?? ""}
                placeholder="50"
                onChange={(event) =>
                  setLimit(index, {
                    up_to: event.target.value === "" ? null : Number(event.target.value),
                  })
                }
                className="h-8 w-28 text-[11px]"
              />
            </div>
            <div>
              <Label>At most per 30 days</Label>
              <Input
                type="number"
                min={1}
                value={limit.max_per_30_days ?? ""}
                placeholder="no cap"
                onChange={(event) =>
                  setLimit(index, {
                    max_per_30_days: event.target.value === "" ? null : Number(event.target.value),
                  })
                }
                className="h-8 w-28 text-[11px]"
              />
            </div>
            <Button
              size="sm"
              variant="ghost"
              title="Remove limit"
              onClick={() =>
                update({
                  auto_approve: (value.auto_approve ?? []).filter(
                    (_, position) => position !== index,
                  ),
                })
              }
            >
              ✕
            </Button>
          </div>
        ))}
        <datalist id="guardrail-limit-actions">
          {catalog.data?.actions
            .filter((item) => item.family === "money" || item.family === "account")
            .map((item) => (
              <option key={item.action} value={item.action} />
            ))}
        </datalist>
        <Button
          size="sm"
          variant="outline"
          onClick={() =>
            update({
              auto_approve: [
                ...(value.auto_approve ?? []),
                { actions: ["process_refund"], up_to: 50, max_per_30_days: null },
              ],
            })
          }
        >
          Add limit
        </Button>
      </div>

      <div className="w-56">
        <Label htmlFor="approval-ttl">A request for approval lapses after</Label>
        <div className="flex items-center gap-2">
          <Input
            id="approval-ttl"
            type="number"
            min={1}
            max={720}
            value={value.approval_ttl_hours ?? 24}
            onChange={(event) => update({ approval_ttl_hours: Number(event.target.value) })}
            className="w-24"
          />
          <span className="label">hours</span>
        </div>
      </div>

      <div className="space-y-2">
        <p className="label">
          Project rules — every one that matches is reported; deny outranks approval
        </p>
        {rules.map((rule, index) => (
          <div key={index} className="space-y-2 border border-border bg-surface-2 p-3">
            <div className="flex flex-wrap items-end gap-2">
              <div>
                <Label>Name</Label>
                <Input
                  value={rule.name}
                  onChange={(event) => setRule(index, { name: event.target.value })}
                  className="h-8 w-40 text-[11px]"
                />
              </div>
              <div className="min-w-[12rem] flex-1">
                <Label>Actions (comma-separated, * for all)</Label>
                <Input
                  list="guardrail-actions"
                  value={(rule.actions ?? ["*"]).join(", ")}
                  onChange={(event) =>
                    setRule(index, {
                      actions: event.target.value
                        .split(",")
                        .map((item) => item.trim())
                        .filter(Boolean),
                    })
                  }
                  className="h-8 text-[11px]"
                />
              </div>
              <div>
                <Label>Decision</Label>
                <Select
                  value={rule.decision}
                  onChange={(event) =>
                    setRule(index, { decision: event.target.value as GuardrailRule["decision"] })
                  }
                  className="h-8 w-44"
                >
                  <option value="deny">deny</option>
                  <option value="require_approval">require approval</option>
                </Select>
              </div>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => update({ rules: rules.filter((_, position) => position !== index) })}
                title="Remove rule"
              >
                ✕
              </Button>
            </div>
            <ConditionEditor
              projectId={projectId}
              value={rule.when}
              onChange={(when) => setRule(index, { when })}
              placeholder='request.amount > 500 and health.band == "at_risk"'
            />
            <div>
              <Label>Tell the agent</Label>
              <Input
                value={rule.message}
                placeholder="Refunds over 500 go to finance."
                onChange={(event) => setRule(index, { message: event.target.value })}
                className="h-8 text-[11px]"
              />
            </div>
          </div>
        ))}
        <datalist id="guardrail-actions">
          {catalog.data?.actions.map((item) => (
            <option key={item.action} value={item.action} />
          ))}
        </datalist>
        <Button
          size="sm"
          variant="outline"
          onClick={() =>
            update({
              rules: [
                ...rules,
                {
                  name: `rule_${rules.length + 1}`,
                  actions: ["*"],
                  when: "",
                  decision: "deny",
                  message: "",
                },
              ],
            })
          }
        >
          Add rule
        </Button>
      </div>
    </div>
  );
}
