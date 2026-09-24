"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label } from "@/components/ui/input";
import { EmptyState, ErrorState, LoadingRow } from "@/components/ui/states";
import { Table, TD, TH, THead, TR } from "@/components/ui/table";
import { api } from "@/lib/api";
import { formatRelative } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { EvalRun, EvalRunSummary, EvalSet, EvalSetDetail, EvalSuggestion } from "@/lib/types";

const pct = (value: number | undefined) =>
  value === undefined ? "—" : `${Math.round(value * 100)}%`;

function Delta({ value, percent = true }: { value: number | undefined; percent?: boolean }) {
  if (value === undefined || value === 0) return <span className="label">±0</span>;
  const shown = percent
    ? `${value > 0 ? "+" : ""}${Math.round(value * 100)}pt`
    : `${value > 0 ? "+" : ""}${value.toFixed(2)}`;
  return (
    <span className={cn("numeric text-[11px]", value > 0 ? "text-success" : "text-danger")}>
      {shown}
    </span>
  );
}

/** Questions with known answers, and every run that scored retrieval against them. */
export function EvaluationPanel({
  projectId,
  focusRun,
}: {
  projectId: string | null;
  focusRun?: string | null;
}) {
  const base = `/v1/projects/${projectId}/evals`;
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<string | null>(null);
  const [runId, setRunId] = useState<string | null>(focusRun ?? null);
  const [name, setName] = useState("");

  useEffect(() => setRunId(focusRun ?? null), [focusRun]);

  const sets = useQuery({
    queryKey: ["eval-sets", projectId],
    queryFn: () => api<EvalSet[]>(base),
    enabled: Boolean(projectId),
  });
  useEffect(() => {
    if (!selected && sets.data?.length) setSelected(sets.data[0].id);
  }, [sets.data, selected]);

  const create = useMutation({
    mutationFn: () => api<EvalSet>(base, { method: "POST", body: { name } }),
    onSuccess: (created) => {
      setName("");
      setSelected(created.id);
      queryClient.invalidateQueries({ queryKey: ["eval-sets"] });
    },
  });

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Evaluation sets</CardTitle>
          <p className="mt-1 text-xs text-muted-foreground">
            Questions whose right answers you know. A run asks every one through the real answer
            path — recording nothing — and scores how often, and how high, the right memory comes
            back.
          </p>
        </CardHeader>
        <CardContent className="space-y-3">
          {sets.isLoading && <LoadingRow />}
          <div className="flex flex-wrap gap-2">
            {sets.data?.map((row) => (
              <button
                key={row.id}
                onClick={() => {
                  setSelected(row.id);
                  setRunId(null);
                }}
                className={cn(
                  "border px-3 py-2 text-left",
                  selected === row.id
                    ? "border-accent bg-accent/5"
                    : "border-border bg-surface hover:border-border-strong",
                )}
              >
                <p className="text-[12px] font-semibold">{row.name}</p>
                <p className="label">
                  {row.cases} cases
                  {row.latest_run &&
                    ` · recall@5 ${pct(row.latest_run.metrics.recall?.["@5"])} · MRR ${row.latest_run.metrics.mrr?.toFixed(2)}`}
                </p>
              </button>
            ))}
          </div>
          <div className="flex gap-2">
            <Input
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="New set, e.g. Support questions"
              className="w-72"
            />
            <Button
              size="sm"
              variant="outline"
              disabled={!name.trim()}
              loading={create.isPending}
              onClick={() => create.mutate()}
            >
              Create set
            </Button>
          </div>
          {create.error && <ErrorState error={create.error} />}
        </CardContent>
      </Card>

      {selected && <SetDetail base={base} setId={selected} runId={runId} onRun={setRunId} />}
      {!selected && sets.data?.length === 0 && (
        <EmptyState
          title="No evaluation sets yet"
          description="Create one, add questions whose answers you know, and run it."
        />
      )}
    </div>
  );
}

function SetDetail({
  base,
  setId,
  runId,
  onRun,
}: {
  base: string;
  setId: string;
  runId: string | null;
  onRun: (id: string) => void;
}) {
  const queryClient = useQueryClient();
  const detail = useQuery({
    queryKey: ["eval-set", setId],
    queryFn: () => api<EvalSetDetail>(`${base}/${setId}`),
  });
  const [label, setLabel] = useState("");
  const run = useMutation({
    mutationFn: () =>
      api<EvalRun>(`${base}/${setId}/runs`, {
        method: "POST",
        body: { label: label || null, k: 10, wait: true },
      }),
    onSuccess: (created) => {
      setLabel("");
      onRun(created.id);
      queryClient.invalidateQueries({ queryKey: ["eval-set", setId] });
      queryClient.invalidateQueries({ queryKey: ["eval-sets"] });
      queryClient.invalidateQueries({ queryKey: ["quality"] });
    },
  });

  if (detail.isLoading) return <LoadingRow />;
  if (detail.error) return <ErrorState error={detail.error} />;
  const data = detail.data!;
  const shownRun = runId ?? data.runs.find((item) => item.status === "succeeded")?.id ?? null;

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-4">
          <div>
            <CardTitle>
              {data.name} · {data.cases} cases
            </CardTitle>
            <p className="mt-1 text-xs text-muted-foreground">
              Expected memory ids are exact but die when a customer is reprocessed; expected phrases
              survive it and match any memory that mentions every word, in any form.
            </p>
          </div>
          <div className="flex items-end gap-2">
            <div>
              <Label htmlFor="run-label">Label</Label>
              <Input
                id="run-label"
                value={label}
                onChange={(event) => setLabel(event.target.value)}
                placeholder="e.g. after vocabulary"
                className="w-48"
              />
            </div>
            <Button
              size="sm"
              disabled={data.cases === 0}
              loading={run.isPending}
              onClick={() => run.mutate()}
            >
              Run evaluation
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          {run.error && <ErrorState error={run.error} />}
          <CaseTable base={base} setId={setId} detail={data} />
          <AddCase base={base} setId={setId} />
        </CardContent>
      </Card>

      {data.runs.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Runs</CardTitle>
          </CardHeader>
          <Table>
            <THead>
              <TR>
                <TH>Run</TH>
                <TH className="text-right">Recall@5</TH>
                <TH className="text-right">Hit@1</TH>
                <TH className="text-right">MRR</TH>
                <TH className="text-right">Cited</TH>
                <TH className="text-right">vs previous</TH>
              </TR>
            </THead>
            <tbody>
              {data.runs.map((item) => (
                <RunRow
                  key={item.id}
                  run={item}
                  active={item.id === shownRun}
                  onOpen={() => onRun(item.id)}
                />
              ))}
            </tbody>
          </Table>
        </Card>
      )}

      {shownRun && <RunDetail base={base} runId={shownRun} />}
    </div>
  );
}

function RunRow({
  run,
  active,
  onOpen,
}: {
  run: EvalRunSummary;
  active: boolean;
  onOpen: () => void;
}) {
  return (
    <TR className={cn("cursor-pointer", active && "bg-accent/5")} onClick={onOpen}>
      <TD>
        <p className="text-[12px] font-semibold">{run.label ?? "unlabelled"}</p>
        <p className="label">
          {run.status} · {formatRelative(run.created_at)} · k={run.k}
        </p>
      </TD>
      <TD className="numeric text-right text-[12px]">{pct(run.metrics.recall?.["@5"])}</TD>
      <TD className="numeric text-right text-[12px]">{pct(run.metrics.hit?.["@1"])}</TD>
      <TD className="numeric text-right text-[12px]">{run.metrics.mrr?.toFixed(2) ?? "—"}</TD>
      <TD className="numeric text-right text-[12px]">{pct(run.metrics.citation_hit_rate)}</TD>
      <TD className="text-right">
        {run.comparison ? (
          <span className="flex items-center justify-end gap-2">
            <Delta value={run.comparison.recall?.["@5"]} />
            {run.comparison.regressed && (
              <Badge className="border-danger/70 bg-danger/10 text-danger">regressed</Badge>
            )}
          </span>
        ) : (
          <span className="label">baseline</span>
        )}
      </TD>
    </TR>
  );
}

function RunDetail({ base, runId }: { base: string; runId: string }) {
  const run = useQuery({
    queryKey: ["eval-run", runId],
    queryFn: () => api<EvalRun>(`${base}/runs/${runId}`),
  });
  if (!run.data) return run.isLoading ? <LoadingRow /> : null;
  const data = run.data;
  return (
    <Card>
      <CardHeader>
        <CardTitle>{data.label ?? "Run"} — question by question</CardTitle>
        {data.comparison &&
        (data.comparison.newly_missed?.length || data.comparison.newly_found?.length) ? (
          <p className="mt-1 text-xs">
            {data.comparison.newly_missed?.length ? (
              <span className="text-danger">
                {data.comparison.newly_missed.length} newly missed ·{" "}
              </span>
            ) : null}
            {data.comparison.newly_found?.length ? (
              <span className="text-success">{data.comparison.newly_found.length} newly found</span>
            ) : null}
          </p>
        ) : null}
      </CardHeader>
      <CardContent className="space-y-2">
        {data.results.map((result) => (
          <details key={result.case_id} className="bg-surface-2 px-3 py-2">
            <summary className="flex cursor-pointer flex-wrap items-center gap-3">
              <Badge
                className={
                  result.first_rank === null
                    ? "border-danger/70 bg-danger/10 text-danger"
                    : result.first_rank === 1
                      ? "border-success/70 bg-success/10 text-success"
                      : ""
                }
              >
                {result.first_rank === null ? "missed" : `rank ${result.first_rank}`}
              </Badge>
              <span className="flex-1 text-[12px]">{result.question}</span>
              {result.cited_hit && <span className="label text-success">cited</span>}
            </summary>
            <div className="mt-2 space-y-2 text-[11px]">
              <div>
                <p className="label">Expected</p>
                {result.expected.map((item) => (
                  <p key={`${item.kind}-${item.target}`} className="font-mono">
                    {item.kind}: {item.target} →{" "}
                    {item.rank === null ? "not in the top " + data.k : `rank ${item.rank}`}
                  </p>
                ))}
              </div>
              <div>
                <p className="label">What came back</p>
                <ol className="space-y-px">
                  {result.retrieved.map((memory, index) => (
                    <li key={memory.id} className="flex gap-2">
                      <span className="numeric w-5 text-muted-foreground">{index + 1}</span>
                      <span className="flex-1">{memory.content}</span>
                      <span className="label">{memory.strategies.join(", ")}</span>
                    </li>
                  ))}
                </ol>
              </div>
            </div>
          </details>
        ))}
      </CardContent>
    </Card>
  );
}

function CaseTable({
  base,
  setId,
  detail,
}: {
  base: string;
  setId: string;
  detail: EvalSetDetail;
}) {
  const queryClient = useQueryClient();
  const remove = useMutation({
    mutationFn: (caseId: string) => api(`${base}/${setId}/cases/${caseId}`, { method: "DELETE" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["eval-set", setId] }),
  });
  if (detail.case_list.length === 0)
    return <p className="label">No cases yet — add one below, or pick from real questions.</p>;
  return (
    <Table>
      <THead>
        <TR>
          <TH>Question</TH>
          <TH className="w-40">Customer</TH>
          <TH>Right answer</TH>
          <TH className="w-12" />
        </TR>
      </THead>
      <tbody>
        {detail.case_list.map((item) => (
          <TR key={item.id}>
            <TD className="text-[12px]">{item.question}</TD>
            <TD className="font-mono text-[11px]">{item.customer_id}</TD>
            <TD className="text-[11px] text-muted-foreground">
              {[
                ...item.expected_phrases.map((phrase) => `“${phrase}”`),
                ...item.expected_memory_ids,
              ].join(", ")}
            </TD>
            <TD>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => remove.mutate(item.id)}
                title="Remove case"
              >
                ✕
              </Button>
            </TD>
          </TR>
        ))}
      </tbody>
    </Table>
  );
}

function AddCase({ base, setId }: { base: string; setId: string }) {
  const queryClient = useQueryClient();
  const [customer, setCustomer] = useState("");
  const [question, setQuestion] = useState("");
  const [phrases, setPhrases] = useState("");
  const [memoryIds, setMemoryIds] = useState<string[]>([]);
  const [suggest, setSuggest] = useState(false);
  const suggestions = useQuery({
    queryKey: ["eval-suggestions", base],
    queryFn: () => api<EvalSuggestion[]>(`${base}/suggestions`, { query: { limit: 15 } }),
    enabled: suggest,
  });
  const add = useMutation({
    mutationFn: () =>
      api(`${base}/${setId}/cases`, {
        method: "POST",
        body: {
          cases: [
            {
              customer_id: customer,
              question,
              expected_phrases: phrases
                .split(",")
                .map((item) => item.trim())
                .filter(Boolean),
              expected_memory_ids: memoryIds,
            },
          ],
        },
      }),
    onSuccess: () => {
      setQuestion("");
      setPhrases("");
      setMemoryIds([]);
      queryClient.invalidateQueries({ queryKey: ["eval-set", setId] });
      queryClient.invalidateQueries({ queryKey: ["eval-sets"] });
    },
  });

  return (
    <div className="space-y-3 border-t border-border pt-4">
      <div className="flex items-center justify-between">
        <p className="label">Add a case</p>
        <button className="label hover:text-accent" onClick={() => setSuggest(!suggest)}>
          {suggest ? "hide real questions" : "pick from real questions"}
        </button>
      </div>

      {suggest && (
        <div className="max-h-80 space-y-2 overflow-auto border border-border p-2">
          {suggestions.isLoading && <LoadingRow />}
          {suggestions.data?.length === 0 && (
            <p className="label">Nobody has asked a question yet.</p>
          )}
          {suggestions.data?.map((item, index) => (
            <div key={`${item.question}-${index}`} className="bg-surface-2 p-2">
              <p className="text-[12px] font-semibold">{item.question}</p>
              <p className="label mb-1">
                {item.customer_id} · pick the memory that actually answers it
              </p>
              {item.retrieved.map((memory) => (
                <button
                  key={memory.id}
                  onClick={() => {
                    setCustomer(item.customer_id);
                    setQuestion(item.question);
                    setMemoryIds([memory.id]);
                  }}
                  className="block w-full px-2 py-1 text-left text-[11px] hover:bg-accent/10"
                >
                  {memory.content}
                </button>
              ))}
            </div>
          ))}
        </div>
      )}

      <div className="flex flex-wrap items-end gap-2">
        <div>
          <Label htmlFor="case-customer">Customer</Label>
          <Input
            id="case-customer"
            value={customer}
            onChange={(event) => setCustomer(event.target.value)}
            placeholder="cus_123"
            className="w-40"
          />
        </div>
        <div className="min-w-[16rem] flex-1">
          <Label htmlFor="case-question">Question</Label>
          <Input
            id="case-question"
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            placeholder="Is the integration broken?"
          />
        </div>
        <div className="min-w-[14rem] flex-1">
          <Label htmlFor="case-phrases">Right answer mentions (comma-separated)</Label>
          <Input
            id="case-phrases"
            value={phrases}
            onChange={(event) => setPhrases(event.target.value)}
            placeholder="shopify sync, fails"
          />
        </div>
        <Button
          size="sm"
          variant="outline"
          disabled={!customer || !question || (!phrases.trim() && memoryIds.length === 0)}
          loading={add.isPending}
          onClick={() => add.mutate()}
        >
          Add case
        </Button>
      </div>
      {memoryIds.length > 0 && <p className="label">expected memory: {memoryIds.join(", ")}</p>}
      {add.error && <ErrorState error={add.error} />}
    </div>
  );
}
