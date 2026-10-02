"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { Table, TD, TH, THead, TR } from "@/components/ui/table";
import { api } from "@/lib/api";
import { contractYaml } from "@/lib/contract-yaml";
import { cn } from "@/lib/utils";
import {
  CONTRACT_FIELD_TYPES,
  type ContractDefinition,
  type ContractDraft,
  type ContractFieldRule,
  type ContractFieldType,
  type ContractMode,
  type ContractTestResult,
  type EventContract,
  type EventRecord,
  type Page,
} from "@/lib/types";

export const MODE_STYLES: Record<ContractMode, string> = {
  enforce: "border-danger/60 bg-danger/10 text-danger",
  warn: "border-warning/60 bg-warning/10 text-warning",
  off: "border-border bg-surface-2 text-muted-foreground",
};

const MODE_HELP: Record<ContractMode, string> = {
  warn: "Keep an event that breaks the contract, and report how it broke it.",
  enforce:
    "Refuse an event that breaks the contract with 422 contract_violation — nothing is stored. Integration webhooks and agent turns are never refused.",
  off: "Check nothing; still read the text field and importance from the contract.",
};

interface Row {
  id: number;
  path: string;
  type: ContractFieldType;
  required: boolean;
  /** Allowed values, comma separated. */
  values: string;
  minimum: string;
  maximum: string;
  maxLength: string;
  pattern: string;
  description: string;
  /** From a draft: the traffic sent this field as more than one type. */
  seen?: Record<string, number>;
}

interface State {
  eventType: string;
  mode: ContractMode;
  description: string;
  textField: string;
  importance: string;
  allowExtra: boolean;
  rows: Row[];
}

let nextRow = 0;

function blankRow(path = ""): Row {
  return {
    id: nextRow++,
    path,
    type: "string",
    required: false,
    values: "",
    minimum: "",
    maximum: "",
    maxLength: "",
    pattern: "",
    description: "",
  };
}

function stateOf(definition: ContractDefinition, eventType: string): State {
  const required = new Set(definition.required ?? []);
  const fields = Object.entries(definition.fields ?? {});
  const rows: Row[] = fields.map(([path, spec]) => ({
    id: nextRow++,
    path,
    type: (spec.type ?? "any") as ContractFieldType,
    required: required.has(path) || Boolean(spec.required),
    values: (spec.enum ?? []).map(String).join(", "),
    minimum: spec.minimum?.toString() ?? "",
    maximum: spec.maximum?.toString() ?? "",
    maxLength: spec.max_length?.toString() ?? "",
    pattern: spec.pattern ?? "",
    description: spec.description ?? "",
    seen: spec.seen,
  }));
  for (const path of required) {
    if (!fields.some(([name]) => name === path))
      rows.push({ ...blankRow(path), type: "any", required: true });
  }
  return {
    eventType: definition.event_type ?? eventType,
    mode: definition.mode ?? "warn",
    description: definition.description ?? "",
    textField: definition.text_field ?? "",
    importance: definition.importance?.toString() ?? "",
    allowExtra: definition.allow_extra !== false,
    rows: rows.length ? rows : [blankRow()],
  };
}

function definitionOf(state: State): ContractDefinition {
  const fields: Record<string, ContractFieldRule> = {};
  for (const row of state.rows) {
    const path = row.path.trim();
    if (!path) continue;
    const spec: ContractFieldRule = { type: row.type };
    const values = row.values
      .split(",")
      .map((value) => value.trim())
      .filter(Boolean);
    if (values.length) {
      const numeric =
        (row.type === "number" || row.type === "integer") &&
        values.every((value) => !Number.isNaN(Number(value)));
      spec.enum = numeric ? values.map(Number) : values;
    }
    if (row.minimum.trim()) spec.minimum = Number(row.minimum);
    if (row.maximum.trim()) spec.maximum = Number(row.maximum);
    if (row.maxLength.trim()) spec.max_length = Number(row.maxLength);
    if (row.pattern) spec.pattern = row.pattern;
    if (row.description.trim()) spec.description = row.description.trim();
    fields[path] = spec;
  }
  return {
    event_type: state.eventType.trim(),
    mode: state.mode,
    description: state.description.trim(),
    required: state.rows
      .filter((row) => row.required && row.path.trim())
      .map((row) => row.path.trim()),
    fields,
    text_field: state.textField || null,
    importance: state.importance.trim() ? Number(state.importance) : null,
    allow_extra: state.allowExtra,
  };
}

/** The event a YAML contract names, so a check can be addressed before it is saved. */
function yamlEvent(yaml: string): string | null {
  return /^event(?:_type)?:\s*["']?([a-z0-9_.:-]+)/im.exec(yaml)?.[1] ?? null;
}

const SAMPLE = `{
  "amount": 499,
  "currency": "INR",
  "details": { "reason": "The card was declined by the bank." }
}`;

/** Write a contract — as fields or as YAML — draft one from real traffic, and check a payload
 * against it before saving. The server compiles every version; what it refuses is shown here. */
export function ContractEditor({
  projectId,
  eventType,
  existing,
  onSaved,
  onCancel,
}: {
  projectId: string;
  /** Fixed when the type is known: an existing contract, or a type picked from coverage. */
  eventType?: string;
  existing?: EventContract | null;
  onSaved: (contract: EventContract) => void;
  onCancel?: () => void;
}) {
  const [state, setState] = useState<State>(() =>
    stateOf(existing?.definition ?? {}, eventType ?? ""),
  );
  const [view, setView] = useState<"fields" | "yaml">("fields");
  const [yaml, setYaml] = useState("");
  const [payload, setPayload] = useState(SAMPLE);
  // The test starts from what this type really looks like: its latest event, until edited.
  const edited = useRef(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [drafted, setDrafted] = useState<number | null>(null);

  const base = `/v1/projects/${projectId}/contracts`;
  const fixedType = existing?.event_type ?? eventType;

  const latest = useQuery({
    queryKey: ["contract-sample", projectId, fixedType],
    queryFn: () =>
      api<Page<EventRecord>>(`/v1/projects/${projectId}/events`, {
        query: { event_type: fixedType, limit: 1 },
      }),
    enabled: Boolean(fixedType),
    staleTime: 60_000,
  });
  const sample = latest.data?.data[0]?.data;
  useEffect(() => {
    if (sample && Object.keys(sample).length && !edited.current) {
      setPayload(JSON.stringify(sample, null, 2));
    }
  }, [sample]);
  const typeForChecks = (view === "yaml" ? yamlEvent(yaml) : null) ?? state.eventType.trim();

  const update = (patch: Partial<State>) => setState((current) => ({ ...current, ...patch }));
  const updateRow = (id: number, patch: Partial<Row>) =>
    setState((current) => ({
      ...current,
      rows: current.rows.map((row) =>
        row.id === id ? { ...row, ...patch, seen: undefined } : row,
      ),
    }));

  const save = useMutation({
    mutationFn: () => {
      const body = view === "yaml" ? { yaml } : definitionOf(state);
      return fixedType && existing
        ? api<EventContract>(`${base}/${encodeURIComponent(fixedType)}`, { method: "PUT", body })
        : api<EventContract>(base, { method: "POST", body });
    },
    onSuccess: (saved) => {
      setProblem(null);
      onSaved(saved);
    },
    onError: (error) => setProblem(error instanceof Error ? error.message : "Not saved."),
  });

  const draft = useMutation({
    mutationFn: () =>
      api<ContractDraft>(`${base}/${encodeURIComponent(state.eventType.trim())}/draft`, {
        method: "POST",
      }),
    onSuccess: (result) => {
      setProblem(null);
      setDrafted(result.samples);
      // A draft is always "warn"; keep the mode already chosen.
      setState((current) => ({ ...stateOf(result, current.eventType), mode: current.mode }));
      setView("fields");
    },
    onError: (error) => setProblem(error instanceof Error ? error.message : "No draft."),
  });

  /** YAML → fields goes through the server's own parser, so the form shows what it read. */
  const readYaml = useMutation({
    mutationFn: () =>
      api<ContractTestResult>(`${base}/${encodeURIComponent(typeForChecks)}/test`, {
        method: "POST",
        body: { data: {}, yaml },
      }),
    onSuccess: (result) => {
      setProblem(null);
      setState(stateOf(result.definition, result.event_type));
      setView("fields");
    },
    onError: (error) =>
      setProblem(error instanceof Error ? error.message : "The YAML was not read."),
  });

  const test = useMutation({
    mutationFn: (data: Record<string, unknown>) =>
      api<ContractTestResult>(`${base}/${encodeURIComponent(typeForChecks)}/test`, {
        method: "POST",
        body: view === "yaml" ? { data, yaml } : { data, contract: definitionOf(state) },
      }),
  });

  function runTest() {
    let data: Record<string, unknown>;
    try {
      data = JSON.parse(payload);
    } catch (error) {
      setProblem(`The payload is not JSON: ${(error as Error).message}`);
      return;
    }
    setProblem(null);
    test.mutate(data);
  }

  function switchTo(next: "fields" | "yaml") {
    if (next === view) return;
    if (next === "yaml") {
      setYaml(contractYaml(definitionOf(state)));
      setView("yaml");
    } else {
      readYaml.mutate();
    }
  }

  const textChoices = state.rows
    .filter((row) => row.path.trim() && (row.type === "string" || row.type === "any"))
    .map((row) => row.path.trim());
  if (state.textField && !textChoices.includes(state.textField)) textChoices.push(state.textField);

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex border border-border">
          {(["fields", "yaml"] as const).map((name) => (
            <button
              key={name}
              type="button"
              onClick={() => switchTo(name)}
              className={cn(
                "px-3 py-1.5 font-mono text-[10px] uppercase tracking-label transition-colors",
                view === name
                  ? "bg-accent text-accent-foreground"
                  : "text-muted-foreground hover:bg-surface-2 hover:text-foreground",
              )}
            >
              {name === "fields" ? "Fields" : "YAML"}
            </button>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {drafted !== null && <span className="label">Drafted from {drafted} recent events</span>}
          <Button
            type="button"
            size="sm"
            variant="outline"
            loading={draft.isPending}
            disabled={!state.eventType.trim() || view === "yaml"}
            onClick={() => draft.mutate()}
            title="Infer required fields, types, allowed values and the text field from this type's recent events"
          >
            Generate from recent events
          </Button>
        </div>
      </div>

      {view === "fields" ? (
        <>
          <div className="grid gap-4 md:grid-cols-[1.2fr_1fr_1fr]">
            <div>
              <Label htmlFor="contract-type">Event type</Label>
              <Input
                id="contract-type"
                value={state.eventType}
                disabled={Boolean(fixedType)}
                placeholder="payment_failed"
                onChange={(event) => update({ eventType: event.target.value })}
              />
            </div>
            <div>
              <Label htmlFor="contract-mode">Mode</Label>
              <Select
                id="contract-mode"
                value={state.mode}
                onChange={(event) => update({ mode: event.target.value as ContractMode })}
              >
                <option value="warn">Warn</option>
                <option value="enforce">Enforce</option>
                <option value="off">Off</option>
              </Select>
            </div>
            <div>
              <Label htmlFor="contract-importance">Importance</Label>
              <Input
                id="contract-importance"
                type="number"
                min={0}
                max={1}
                step={0.05}
                placeholder="decided per event"
                value={state.importance}
                onChange={(event) => update({ importance: event.target.value })}
              />
            </div>
          </div>
          <p className="-mt-2 text-xs text-muted-foreground">{MODE_HELP[state.mode]}</p>

          <div className="grid gap-4 md:grid-cols-[2fr_1fr]">
            <div>
              <Label htmlFor="contract-description">Description</Label>
              <Input
                id="contract-description"
                value={state.description}
                placeholder="A card or bank payment did not go through."
                onChange={(event) => update({ description: event.target.value })}
              />
            </div>
            <div>
              <Label htmlFor="contract-text">Text field</Label>
              <Select
                id="contract-text"
                value={state.textField}
                className="normal-case tracking-normal"
                onChange={(event) => update({ textField: event.target.value })}
              >
                <option value="">— found by name —</option>
                {textChoices.map((path) => (
                  <option key={path} value={path}>
                    {path}
                  </option>
                ))}
              </Select>
            </div>
          </div>

          <div className="border border-border">
            <Table className="min-w-[900px]">
              <THead>
                <TR>
                  <TH className="w-52">Field path</TH>
                  <TH className="w-36">Type</TH>
                  <TH className="w-20">Required</TH>
                  <TH className="min-w-[12rem]">Allowed values</TH>
                  <TH className="w-24">Min</TH>
                  <TH className="w-24">Max</TH>
                  <TH className="w-24">Max length</TH>
                  <TH className="w-36">Pattern</TH>
                  <TH className="w-10" />
                </TR>
              </THead>
              <tbody>
                {state.rows.map((row) => {
                  const numeric =
                    row.type === "number" || row.type === "integer" || row.type === "any";
                  return (
                    <TR key={row.id}>
                      <TD className="py-2">
                        <Input
                          aria-label="Field path"
                          value={row.path}
                          placeholder="details.reason"
                          className="h-8 normal-case"
                          onChange={(event) => updateRow(row.id, { path: event.target.value })}
                        />
                        {row.seen && (
                          <p className="mt-1 text-[10px] leading-snug text-warning">
                            Sent as{" "}
                            {Object.entries(row.seen)
                              .map(([kind, count]) => `${kind} ×${count}`)
                              .join(", ")}{" "}
                            — pick one.
                          </p>
                        )}
                      </TD>
                      <TD className="py-2">
                        <Select
                          aria-label="Type"
                          value={row.type}
                          className="h-8 normal-case tracking-normal"
                          onChange={(event) =>
                            updateRow(row.id, { type: event.target.value as ContractFieldType })
                          }
                        >
                          {CONTRACT_FIELD_TYPES.map((kind) => (
                            <option key={kind} value={kind}>
                              {kind}
                            </option>
                          ))}
                        </Select>
                      </TD>
                      <TD className="py-2 text-center">
                        <input
                          type="checkbox"
                          aria-label="Required"
                          checked={row.required}
                          onChange={(event) =>
                            updateRow(row.id, { required: event.target.checked })
                          }
                          className="h-4 w-4 accent-[hsl(var(--accent))]"
                        />
                      </TD>
                      <TD className="py-2">
                        <Input
                          aria-label="Allowed values"
                          value={row.values}
                          placeholder="any"
                          className="h-8 normal-case"
                          onChange={(event) => updateRow(row.id, { values: event.target.value })}
                        />
                      </TD>
                      <TD className="py-2">
                        <Input
                          aria-label="Minimum"
                          type="number"
                          disabled={!numeric}
                          value={row.minimum}
                          className="h-8"
                          onChange={(event) => updateRow(row.id, { minimum: event.target.value })}
                        />
                      </TD>
                      <TD className="py-2">
                        <Input
                          aria-label="Maximum"
                          type="number"
                          disabled={!numeric}
                          value={row.maximum}
                          className="h-8"
                          onChange={(event) => updateRow(row.id, { maximum: event.target.value })}
                        />
                      </TD>
                      <TD className="py-2">
                        <Input
                          aria-label="Max length"
                          type="number"
                          min={1}
                          value={row.maxLength}
                          className="h-8"
                          onChange={(event) => updateRow(row.id, { maxLength: event.target.value })}
                        />
                      </TD>
                      <TD className="py-2">
                        <Input
                          aria-label="Pattern"
                          value={row.pattern}
                          placeholder="—"
                          className="h-8 normal-case"
                          onChange={(event) => updateRow(row.id, { pattern: event.target.value })}
                        />
                      </TD>
                      <TD className="py-2">
                        <button
                          type="button"
                          aria-label="Remove field"
                          title="Remove this field"
                          onClick={() =>
                            setState((current) => ({
                              ...current,
                              rows: current.rows.filter((item) => item.id !== row.id),
                            }))
                          }
                          className="font-mono text-sm text-muted-foreground hover:text-danger"
                        >
                          ×
                        </button>
                      </TD>
                    </TR>
                  );
                })}
              </tbody>
            </Table>
          </div>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => update({ rows: [...state.rows, blankRow()] })}
            >
              Add field
            </Button>
            <label className="flex items-center gap-2 text-xs text-muted-foreground">
              <input
                type="checkbox"
                checked={!state.allowExtra}
                onChange={(event) => update({ allowExtra: !event.target.checked })}
                className="h-4 w-4 accent-[hsl(var(--accent))]"
              />
              Flag fields the contract does not name
            </label>
          </div>
        </>
      ) : (
        <div>
          <Label htmlFor="contract-yaml">Contract as YAML</Label>
          <Textarea
            id="contract-yaml"
            rows={14}
            spellCheck={false}
            value={yaml}
            onChange={(event) => setYaml(event.target.value)}
            className="text-[11px]"
          />
          <p className="mt-1.5 text-xs text-muted-foreground">
            The same file you keep next to your code. Saved as written; switching back to Fields
            shows how the server read it.
          </p>
        </div>
      )}

      {problem && (
        <p className="border-l-2 border-danger bg-danger/[0.06] px-3 py-2 font-mono text-[11px] text-danger">
          {problem}
        </p>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="button"
          size="sm"
          loading={save.isPending}
          disabled={view === "fields" ? !state.eventType.trim() : !yaml.trim()}
          onClick={() => save.mutate()}
        >
          {existing ? `Save as version ${existing.version + 1}` : "Save contract"}
        </Button>
        {onCancel && (
          <Button type="button" size="sm" variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
        )}
        {existing && (
          <span className="label">An unchanged contract keeps version {existing.version}.</span>
        )}
      </div>

      <div className="space-y-3 border-t border-dashed border-border pt-4">
        <div>
          <p className="label-strong">Test a payload</p>
          <p className="mt-1 text-xs text-muted-foreground">
            Checked against the contract as it is written above — saved or not. Nothing is sent.
            {sample && !edited.current ? " Filled in from the latest event of this type." : ""}
          </p>
        </div>
        <Textarea
          aria-label="Payload to test"
          rows={6}
          spellCheck={false}
          value={payload}
          onChange={(event) => {
            edited.current = true;
            setPayload(event.target.value);
          }}
          className="text-[11px]"
        />
        <Button
          type="button"
          size="sm"
          variant="secondary"
          loading={test.isPending}
          disabled={!typeForChecks}
          onClick={runTest}
        >
          Check payload
        </Button>
        {test.error && (
          <p className="font-mono text-[11px] text-danger">
            {test.error instanceof Error ? test.error.message : "The check failed."}
          </p>
        )}
        {test.data && <CheckResult result={test.data} />}
      </div>
    </div>
  );
}

function CheckResult({ result }: { result: ContractTestResult }) {
  return (
    <div
      className={cn(
        "space-y-2 border-l-2 px-3 py-2",
        result.valid ? "border-success bg-success/5" : "border-danger bg-danger/[0.05]",
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        <Badge
          className={
            result.valid ? "border-success/60 text-success" : "border-danger/60 text-danger"
          }
        >
          {result.valid
            ? "keeps the contract"
            : `${result.violations.length} violation${result.violations.length === 1 ? "" : "s"}`}
        </Badge>
        {result.would_refuse && <Badge className={MODE_STYLES.enforce}>would be refused</Badge>}
        {!result.valid && !result.would_refuse && result.mode === "warn" && (
          <span className="label">kept, and reported</span>
        )}
      </div>
      {result.violations.map((violation, index) => (
        <p key={`${violation.path}-${index}`} className="text-[12px] leading-snug">
          <span className="font-mono text-[11px] text-foreground">{violation.path}</span>{" "}
          <span className="text-muted-foreground">— {violation.message}</span>
        </p>
      ))}
      {result.text && (
        <p className="text-[11px] text-muted-foreground">
          Text read: <span className="font-mono">{result.text}</span>
        </p>
      )}
    </div>
  );
}
