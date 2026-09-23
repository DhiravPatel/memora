"use client";

import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

import { MemoryTypeBadge, ScoreBar } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/input";
import { useSession } from "@/hooks/use-session";
import { api } from "@/lib/api";
import { percent } from "@/lib/format";
import type { QueryResponse } from "@/lib/types";

const SUGGESTIONS = [
  "Why did this customer downgrade?",
  "What problems has this customer experienced recently?",
  "How should we contact them?",
  "Are they at risk of churning?",
  "Tell me about this customer",
];

export function AskMemory({ customerId }: { customerId: string }) {
  const { projectId } = useSession();
  const [query, setQuery] = useState(SUGGESTIONS[0]!);

  const ask = useMutation({
    mutationFn: () =>
      api<QueryResponse>(`/v1/projects/${projectId}/playground/query`, {
        method: "POST",
        body: { customer_id: customerId, query, limit: 10, include_trace: true },
      }),
  });

  return (
    <div className="space-y-5">
      <Card>
        <CardHeader className="flex items-center justify-between">
          <CardTitle>Ask memory</CardTitle>
          <span className="label">deterministic · no model call</span>
        </CardHeader>
        <CardContent className="space-y-3">
          <Textarea
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Ask anything about this customer…"
          />
          <div className="flex flex-wrap items-center gap-2">
            <Button onClick={() => ask.mutate()} loading={ask.isPending} disabled={query.length < 3}>
              Run query
            </Button>
            {SUGGESTIONS.map((suggestion) => (
              <button
                key={suggestion}
                onClick={() => setQuery(suggestion)}
                className="border border-border bg-surface-2 px-2 py-1 font-mono text-[10px] uppercase tracking-label text-muted-foreground transition-colors hover:border-accent hover:text-accent"
              >
                {suggestion.length > 34 ? `${suggestion.slice(0, 32)}…` : suggestion}
              </button>
            ))}
          </div>
          {ask.error && (
            <p className="font-mono text-xs text-danger">
              {ask.error instanceof Error ? ask.error.message : "Query failed."}
            </p>
          )}
        </CardContent>
      </Card>

      {ask.data && (
        <>
          <Card className="border-l-2 border-l-accent">
            <CardHeader className="flex items-center justify-between">
              <CardTitle>Answer</CardTitle>
              <span className="label">
                {ask.data.trace?.answer_strategy ?? "—"} · confidence {percent(ask.data.confidence)}
              </span>
            </CardHeader>
            <CardContent>
              <p className="font-display text-[19px] leading-[1.5]">{ask.data.answer}</p>
              <p className="label mt-3">
                {ask.data.sources.length} source event{ask.data.sources.length === 1 ? "" : "s"} ·{" "}
                {ask.data.memories.length} memories retrieved
              </p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Evidence · why these memories were retrieved</CardTitle>
            </CardHeader>
            <CardContent className="space-y-px bg-border">
              {ask.data.memories.map((memory) => (
                <div key={memory.id} className="bg-surface p-4">
                  <div className="flex flex-wrap items-center gap-2">
                    <MemoryTypeBadge type={memory.type} />
                    <span className="label">{memory.id}</span>
                    {memory.retrieved_by?.map((strategy) => (
                      <span
                        key={strategy}
                        className="border border-accent/50 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-label text-accent"
                      >
                        {strategy}
                      </span>
                    ))}
                  </div>
                  <p className="mt-2 text-sm leading-relaxed">{memory.content}</p>
                  <div className="mt-3 flex flex-wrap gap-5">
                    <Metric label="score" value={memory.score ?? 0} />
                    <Metric label="importance" value={memory.importance} />
                    <Metric label="confidence" value={memory.confidence} />
                  </div>
                </div>
              ))}
              {!ask.data.memories.length && (
                <p className="bg-surface p-4 text-xs text-muted-foreground">
                  No memories were retrieved for this question.
                </p>
              )}
            </CardContent>
          </Card>

          {ask.data.trace && (
            <Card>
              <CardHeader>
                <CardTitle>Retrieval trace</CardTitle>
              </CardHeader>
              <CardContent>
                <pre className="max-h-80 overflow-auto border border-border bg-surface-2 p-3 font-mono text-[10px] leading-relaxed text-muted-foreground">
                  {JSON.stringify(ask.data.trace, null, 2)}
                </pre>
              </CardContent>
            </Card>
          )}
        </>
      )}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <p className="label mb-1">{label}</p>
      <ScoreBar value={value} label={value.toFixed(2)} />
    </div>
  );
}
