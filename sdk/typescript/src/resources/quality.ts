/** Memory quality, retrieval and extraction evaluation (§26 2.1–2.2, 4.3). */

import type { HttpClient } from "../client.js";

/** A memory an extraction case expects or forbids — any combination, at least one field. */
export interface Expectation {
  type?: string;
  /** Words it must say, matched in any form. */
  contains?: string;
  entity?: string;
  sensitivity?: "normal" | "restricted";
  action?: "create" | "merge" | "update" | "conflict" | "ignore";
}

export class Quality {
  constructor(private readonly http: HttpClient) {}

  /** A score, its parts, and a diagnostic with a concrete fix for every part that is off. */
  report(days = 30): Promise<Record<string, any>> {
    return this.http.request({ method: "GET", path: "/v1/quality", query: { days } });
  }

  evalSets(): Promise<Record<string, any>[]> {
    return this.http.request({ method: "GET", path: "/v1/evals" });
  }

  createEvalSet(name: string, description?: string): Promise<Record<string, any>> {
    return this.http.request({ method: "POST", path: "/v1/evals", body: { name, description } });
  }

  /** Cases whose right answers you know: questions (expected memory ids, phrases, or both),
   *  and — with `kind: "extraction"` — events and the memories they should and must not make. */
  addEvalCases(
    setId: string,
    cases: {
      customerId: string;
      kind?: "retrieval" | "extraction";
      question?: string;
      expectedMemoryIds?: string[];
      expectedPhrases?: string[];
      event?: { eventType: string; data?: Record<string, unknown>; occurredAt?: string };
      expect?: Expectation[];
      forbid?: Expectation[];
      expectNothing?: boolean;
      notes?: string;
    }[],
  ): Promise<Record<string, any>[]> {
    return this.http.request({
      method: "POST",
      path: `/v1/evals/${encodeURIComponent(setId)}/cases`,
      body: {
        cases: cases.map((item) => ({
          customer_id: item.customerId,
          kind: item.kind ?? "retrieval",
          question: item.question ?? "",
          expected_memory_ids: item.expectedMemoryIds ?? [],
          expected_phrases: item.expectedPhrases ?? [],
          event: item.event
            ? { event_type: item.event.eventType, data: item.event.data ?? {}, occurred_at: item.event.occurredAt }
            : undefined,
          expect: item.expect ?? [],
          forbid: item.forbid ?? [],
          expect_nothing: item.expectNothing ?? false,
          notes: item.notes,
        })),
      },
    });
  }

  /** Before changing a setting: the set as configured and under `settings` (validated like
   *  a save, never saved). `safe` is false when a passing case would fail. */
  evalRegression(setId: string, settings: Record<string, unknown>, k = 10): Promise<Record<string, any>> {
    return this.http.request({
      method: "POST",
      path: `/v1/evals/${encodeURIComponent(setId)}/regression`,
      body: { settings, k },
    });
  }

  /** Memory quality in one place: retrieval, extraction, duplicates and consistency. */
  scorecard(): Promise<Record<string, any>> {
    return this.http.request({ method: "GET", path: "/v1/evals/scorecard" });
  }

  /** Run a set. `comparison.regressed` is true if any question that used to be answered no
   *  longer is — the flag to fail a CI job on. */
  runEval(
    setId: string,
    options: { label?: string; k?: number; wait?: boolean } = {},
  ): Promise<Record<string, any>> {
    return this.http.request({
      method: "POST",
      path: `/v1/evals/${encodeURIComponent(setId)}/runs`,
      body: { label: options.label ?? null, k: options.k ?? 10, wait: options.wait ?? true },
    });
  }

  evalRun(runId: string): Promise<Record<string, any>> {
    return this.http.request({
      method: "GET",
      path: `/v1/evals/runs/${encodeURIComponent(runId)}`,
    });
  }
}
