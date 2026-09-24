"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Badge, MemoryTypeBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, StatTile } from "@/components/ui/card";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { ErrorState, LoadingRow } from "@/components/ui/states";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { EvalRegression, EvalScorecard, Expectation, ExtractionCaseResult } from "@/lib/types";

const pct = (value: number | null | undefined) =>
  value === null || value === undefined ? "—" : `${Math.round(value * 100)}%`;

const MEMORY_TYPES = [
  "problem",
  "preference",
  "fact",
  "goal",
  "behavior",
  "relationship",
  "subscription",
  "feedback",
  "intent",
];
const ACTIONS = ["create", "merge", "update", "conflict", "ignore"];

/** Memory quality in one place (§26 4.3): retrieval, extraction, duplicates, consistency. */
export function Scorecard({ projectId }: { projectId: string | null }) {
  const card = useQuery({
    queryKey: ["eval-scorecard", projectId],
    queryFn: () => api<EvalScorecard>(`/v1/projects/${projectId}/evals/scorecard`),
    enabled: Boolean(projectId),
  });
  if (card.isLoading) return <LoadingRow label="Loading the scorecard" />;
  if (card.error) return <ErrorState error={card.error} />;
  const data = card.data;
  if (!data) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Memory scorecard</CardTitle>
        <p className="mt-1 text-xs text-muted-foreground">
          From the latest run of every set under the project&apos;s own settings, next to the
          quality report&apos;s duplicate and consistency checks.
        </p>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <StatTile label="Retrieval recall@5" value={pct(data.retrieval.recall_at_5)} />
          <StatTile label="Citation accuracy" value={pct(data.retrieval.citation_accuracy)} />
          <StatTile label="Extraction accuracy" value={pct(data.extraction.accuracy)} />
          <StatTile label="False-memory rate" value={pct(data.extraction.false_memory_rate)} />
          <StatTile label="Type accuracy" value={pct(data.extraction.type_accuracy)} />
          <StatTile
            label="Consolidation accuracy"
            value={pct(data.extraction.consolidation_accuracy)}
          />
          <StatTile
            label="Duplicate control"
            value={
              data.memory.duplicate_control === null ? "—" : `${data.memory.duplicate_control}`
            }
          />
          <StatTile
            label="Consistency"
            value={data.memory.consistency === null ? "—" : `${data.memory.consistency}`}
          />
        </div>
        <p className="label">
          {data.retrieval.questions} questions · {data.extraction.cases} extraction cases ·{" "}
          {data.sets.filter((set) => set.regressed).length} sets regressed
        </p>
        {data.gaps.map((gap) => (
          <p key={gap} className="text-xs text-warning">
            {gap}
          </p>
        ))}
      </CardContent>
    </Card>
  );
}

type Row = Expectation & { mode: "expect" | "forbid" };

/** An event and the memories it should — and must not — become. */
export function AddExtractionCase({ base, setId }: { base: string; setId: string }) {
  const queryClient = useQueryClient();
  const [customer, setCustomer] = useState("");
  const [label, setLabel] = useState("");
  const [eventType, setEventType] = useState("support_message");
  const [message, setMessage] = useState("");
  const [nothing, setNothing] = useState(false);
  const [rows, setRows] = useState<Row[]>([{ mode: "expect", type: "problem", contains: "" }]);

  const add = useMutation({
    mutationFn: () => {
      const clean = (row: Row) =>
        Object.fromEntries(
          Object.entries(row).filter(
            ([key, value]) => key !== "mode" && value !== "" && value !== undefined,
          ),
        );
      return api(`${base}/${setId}/cases`, {
        method: "POST",
        body: {
          cases: [
            {
              customer_id: customer,
              kind: "extraction",
              question: label,
              event: { event_type: eventType, data: { message } },
              expect: nothing ? [] : rows.filter((row) => row.mode === "expect").map(clean),
              forbid: rows.filter((row) => row.mode === "forbid").map(clean),
              expect_nothing: nothing,
            },
          ],
        },
      });
    },
    onSuccess: () => {
      setMessage("");
      setLabel("");
      queryClient.invalidateQueries({ queryKey: ["eval-set", setId] });
      queryClient.invalidateQueries({ queryKey: ["eval-sets"] });
    },
  });

  function update(index: number, patch: Partial<Row>) {
    setRows(rows.map((row, position) => (position === index ? { ...row, ...patch } : row)));
  }

  return (
    <div className="space-y-3 border-t border-border pt-4">
      <p className="label">Add an extraction case — an event, and what it should become</p>
      <div className="flex flex-wrap items-end gap-2">
        <div>
          <Label htmlFor="x-customer">Customer</Label>
          <Input
            id="x-customer"
            value={customer}
            onChange={(e) => setCustomer(e.target.value)}
            placeholder="cus_123"
            className="w-40"
          />
        </div>
        <div>
          <Label htmlFor="x-type">Event type</Label>
          <Input
            id="x-type"
            value={eventType}
            onChange={(e) => setEventType(e.target.value)}
            className="w-48"
          />
        </div>
        <div className="min-w-[14rem] flex-1">
          <Label htmlFor="x-label">Label (optional)</Label>
          <Input
            id="x-label"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="Resolution closes the problem"
          />
        </div>
      </div>
      <div>
        <Label htmlFor="x-message">What the customer wrote</Label>
        <Textarea
          id="x-message"
          rows={2}
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          placeholder="The payroll export works again, thanks for fixing it."
        />
      </div>
      <label className="flex items-center gap-2 text-xs">
        <input type="checkbox" checked={nothing} onChange={(e) => setNothing(e.target.checked)} />
        It should become no memory at all (noise)
      </label>
      {rows.map((row, index) => (
        <div key={index} className="flex flex-wrap items-end gap-2 bg-surface-2 p-2">
          <Select
            value={row.mode}
            onChange={(e) => update(index, { mode: e.target.value as Row["mode"] })}
            className="h-8 w-32"
          >
            <option value="expect">should make</option>
            <option value="forbid">must not make</option>
          </Select>
          <Select
            value={row.type ?? ""}
            onChange={(e) => update(index, { type: e.target.value || undefined })}
            className="h-8 w-36"
          >
            <option value="">any type</option>
            {MEMORY_TYPES.map((type) => (
              <option key={type} value={type}>
                {type}
              </option>
            ))}
          </Select>
          <Input
            value={row.contains ?? ""}
            onChange={(e) => update(index, { contains: e.target.value })}
            placeholder="saying… e.g. payroll export"
            className="h-8 w-56 text-[11px]"
          />
          <Input
            value={row.entity ?? ""}
            onChange={(e) => update(index, { entity: e.target.value })}
            placeholder="naming… e.g. Shopify"
            className="h-8 w-36 text-[11px]"
          />
          <Select
            value={row.sensitivity ?? ""}
            onChange={(e) => update(index, { sensitivity: e.target.value || undefined })}
            className="h-8 w-32"
          >
            <option value="">any sensitivity</option>
            <option value="normal">normal</option>
            <option value="restricted">restricted</option>
          </Select>
          <Select
            value={row.action ?? ""}
            onChange={(e) => update(index, { action: e.target.value || undefined })}
            className="h-8 w-32"
          >
            <option value="">any action</option>
            {ACTIONS.map((action) => (
              <option key={action} value={action}>
                {action}
              </option>
            ))}
          </Select>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setRows(rows.filter((_, position) => position !== index))}
            title="Remove"
          >
            ✕
          </Button>
        </div>
      ))}
      <div className="flex gap-2">
        <Button size="sm" variant="ghost" onClick={() => setRows([...rows, { mode: "expect" }])}>
          + expectation
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={!customer || !eventType || !message.trim() || (!nothing && rows.length === 0)}
          loading={add.isPending}
          onClick={() => add.mutate()}
        >
          Add extraction case
        </Button>
      </div>
      {add.error && <ErrorState error={add.error} />}
    </div>
  );
}

/** One extraction case's result: what the event became, against what it should. */
export function ExtractionResultView({ result }: { result: ExtractionCaseResult }) {
  return (
    <details className="bg-surface-2 px-3 py-2">
      <summary className="flex cursor-pointer flex-wrap items-center gap-3">
        <Badge
          className={
            result.passed
              ? "border-success/70 bg-success/10 text-success"
              : "border-danger/70 bg-danger/10 text-danger"
          }
        >
          {result.passed ? "passed" : "failed"}
        </Badge>
        <Badge>extraction</Badge>
        <span className="flex-1 text-[12px]">{result.label}</span>
      </summary>
      <div className="mt-2 space-y-2 text-[11px]">
        {result.error && <p className="text-danger">{result.error}</p>}
        {result.stop_reason && (
          <p className="text-muted-foreground">Stopped: {result.stop_reason}</p>
        )}
        <div>
          <p className="label">It would become</p>
          {result.planned.length === 0 ? (
            <p className="text-muted-foreground">
              no memory{result.expect_nothing ? " — as expected" : ""}
            </p>
          ) : (
            <ol className="space-y-1">
              {result.planned.map((memory, index) => (
                <li key={index} className="flex flex-wrap items-center gap-2">
                  <MemoryTypeBadge type={memory.type} />
                  <span className="flex-1">{memory.content}</span>
                  <span className="label">
                    {memory.action}
                    {memory.sensitivity === "restricted" ? " · restricted" : ""}
                    {memory.entities.length ? ` · ${memory.entities.join(", ")}` : ""}
                  </span>
                </li>
              ))}
            </ol>
          )}
        </div>
        {result.expected.length > 0 && (
          <div>
            <p className="label">Expected</p>
            {result.expected.map((item, index) => (
              <p key={index} className={cn(item.matched ? "text-success" : "text-danger")}>
                {item.matched ? "✓" : "✗"} {item.described}
                {!item.matched && item.near_miss ? ` — ${item.near_miss}` : ""}
              </p>
            ))}
          </div>
        )}
        {result.forbidden.length > 0 && (
          <div>
            <p className="label">Must not</p>
            {result.forbidden.map((item, index) => (
              <p
                key={index}
                className={cn(item.violated_by.length ? "text-danger" : "text-success")}
              >
                {item.violated_by.length ? "✗" : "✓"} {item.described}
                {item.violated_by.length
                  ? ` — made by #${item.violated_by.map((position) => position + 1).join(", #")}`
                  : ""}
              </p>
            ))}
          </div>
        )}
      </div>
    </details>
  );
}

/** "What would this setting break?" — the set as configured and under proposed settings. */
export function RegressionPanel({ base, setId }: { base: string; setId: string }) {
  const [text, setText] = useState('{\n  "consolidation_similarity": 0.4\n}');
  const [parseError, setParseError] = useState<string | null>(null);
  const regression = useMutation({
    mutationFn: (settings: Record<string, unknown>) =>
      api<EvalRegression>(`${base}/${setId}/regression`, {
        method: "POST",
        body: { settings, k: 10 },
      }),
  });

  function run() {
    try {
      const parsed = JSON.parse(text);
      setParseError(null);
      regression.mutate(parsed);
    } catch {
      setParseError("That is not valid JSON.");
    }
  }

  const data = regression.data;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Try a settings change first</CardTitle>
        <p className="mt-1 text-xs text-muted-foreground">
          The set runs as configured and under the settings below — validated like a save, never
          saved — and every case the change would break is named.
        </p>
      </CardHeader>
      <CardContent className="space-y-3">
        <Textarea
          rows={4}
          value={text}
          onChange={(e) => setText(e.target.value)}
          className="font-mono text-[11px]"
        />
        <Button size="sm" loading={regression.isPending} onClick={run}>
          Run the regression
        </Button>
        {parseError && <p className="text-xs text-danger">{parseError}</p>}
        {regression.error && <ErrorState error={regression.error} />}
        {data && (
          <div className="space-y-2">
            <p className={cn("text-sm", data.safe ? "text-success" : "text-danger")}>
              {data.summary}
            </p>
            <div className="grid gap-3 sm:grid-cols-2">
              {(["current", "proposed"] as const).map((side) => (
                <div key={side} className="border border-border p-3 text-xs">
                  <p className="label-strong">
                    {side === "current" ? "As configured" : "Proposed"}
                  </p>
                  <p>
                    recall@5 {pct(data[side].metrics.recall?.["@5"])} · MRR{" "}
                    {data[side].metrics.mrr?.toFixed(2) ?? "—"}
                  </p>
                  {data[side].metrics.extraction && (
                    <p>
                      extraction {pct(data[side].metrics.extraction?.accuracy)} · false memories{" "}
                      {pct(data[side].metrics.extraction?.false_memory_rate)}
                    </p>
                  )}
                </div>
              ))}
            </div>
            {data.newly_failing.length > 0 && (
              <div>
                <p className="label">Would start failing</p>
                {data.newly_failing.map((item) => (
                  <p key={item.case_id} className="text-xs text-danger">
                    ✗ {item.label} <span className="label">({item.kind})</span>
                  </p>
                ))}
              </div>
            )}
            {data.newly_passing.length > 0 && (
              <div>
                <p className="label">Would start passing</p>
                {data.newly_passing.map((item) => (
                  <p key={item.case_id} className="text-xs text-success">
                    ✓ {item.label} <span className="label">({item.kind})</span>
                  </p>
                ))}
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
