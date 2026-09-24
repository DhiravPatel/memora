/** Memory quality and retrieval evaluation (§26 phase 2). */

import type { HttpClient } from "../client.js";

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

  /** Questions whose right answers you know — expected memory ids, phrases, or both. */
  addEvalCases(
    setId: string,
    cases: {
      customerId: string;
      question: string;
      expectedMemoryIds?: string[];
      expectedPhrases?: string[];
      notes?: string;
    }[],
  ): Promise<Record<string, any>[]> {
    return this.http.request({
      method: "POST",
      path: `/v1/evals/${encodeURIComponent(setId)}/cases`,
      body: {
        cases: cases.map((item) => ({
          customer_id: item.customerId,
          question: item.question,
          expected_memory_ids: item.expectedMemoryIds ?? [],
          expected_phrases: item.expectedPhrases ?? [],
          notes: item.notes,
        })),
      },
    });
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
