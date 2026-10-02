/** Memory contracts (§26 7.1): what each event type must look like.
 *
 * A contract names an event type's required fields, each field's type and bounds, which
 * field carries the human-written text and how important the type is. Events are checked as
 * they arrive: `warn` keeps a violating event and reports it, `enforce` refuses it with a
 * `ContractViolationError`. Reading, testing and drafting need `events:write`; saving and
 * deleting need `admin`. */

import type { HttpClient } from "../client.js";
import type {
  ContractCheck,
  ContractCoverage,
  ContractDefinition,
  ContractReport,
  EventContract,
} from "../types.js";
import { toContractCheck } from "./events.js";

/** How far back a report reads: `24h`, `7d` (the default), `30d` — at most 90 days. */
export type ContractWindow = `${number}h` | `${number}d`;

function toReport(raw: any): ContractReport | null {
  if (!raw) return null;
  return {
    events: raw.events,
    checked: raw.checked,
    violating: raw.violating,
    violations: (raw.violations ?? []).map((item: any) => ({
      path: item.path,
      rule: item.rule,
      expected: item.expected ?? null,
      received: item.received ?? null,
      events: item.events,
      lastSeenAt: item.last_seen_at ?? null,
      latestEventId: item.latest_event_id ?? null,
    })),
    recent: raw.recent ?? [],
  };
}

export function toContract(raw: any): EventContract {
  return {
    id: raw.id,
    eventType: raw.event_type,
    mode: raw.mode,
    version: raw.version,
    definition: raw.definition ?? {},
    rejected: {
      count: raw.rejected?.count ?? 0,
      lastAt: raw.rejected?.last_at ?? null,
      recent: raw.rejected?.recent ?? [],
    },
    events: raw.events ?? null,
    violating: raw.violating ?? null,
    report: toReport(raw.report),
    createdAt: raw.created_at,
    updatedAt: raw.updated_at,
  };
}

function path(eventType: string, suffix = ""): string {
  return `/v1/contracts/${encodeURIComponent(eventType)}${suffix}`;
}

export class Contracts {
  constructor(private readonly http: HttpClient) {}

  /** Every contract, with how many events of its type arrived in the window and broke it. */
  async list(options: { since?: ContractWindow } = {}): Promise<EventContract[]> {
    const raw = await this.http.request<any[]>({
      method: "GET",
      path: "/v1/contracts",
      query: { since: options.since },
    });
    return raw.map(toContract);
  }

  /** One contract and its report: each way events broke it — expected, received, how
   * often, when last — and the latest events that did. */
  async get(eventType: string, options: { since?: ContractWindow } = {}): Promise<EventContract> {
    return toContract(
      await this.http.request<any>({
        method: "GET",
        path: path(eventType),
        query: { since: options.since },
      }),
    );
  }

  /** Create or replace a contract — as an object in the contract language, or as the YAML
   * kept next to your code. The version moves only when the contract changes.
   *
   * ```ts
   * await memora.contracts.save({ yaml: await readFile("contracts/payment_failed.yaml", "utf8") });
   * await memora.contracts.save({ ...contract.definition, mode: "enforce" }, { eventType: "payment_failed" });
   * ```
   */
  async save(
    contract: ContractDefinition | { yaml: string },
    options: { eventType?: string } = {},
  ): Promise<EventContract> {
    const raw = await this.http.request<any>(
      options.eventType
        ? { method: "PUT", path: path(options.eventType), body: contract }
        : { method: "POST", path: "/v1/contracts", body: contract },
    );
    return toContract(raw);
  }

  /** Remove a contract; events of the type are no longer checked. */
  async delete(eventType: string): Promise<void> {
    await this.http.request<void>({ method: "DELETE", path: path(eventType) });
  }

  /** Check a payload without sending it — against the saved contract, or a proposed one.
   * Made for CI: run your fixtures through it before deploying a change.
   *
   * ```ts
   * const check = await memora.contracts.test("payment_failed", fixture);
   * if (!check.valid) throw new Error(check.violations.map((v) => v.message).join("\n"));
   * ```
   */
  async test(
    eventType: string,
    data: Record<string, unknown>,
    options: { contract?: ContractDefinition; yaml?: string } = {},
  ): Promise<ContractCheck> {
    const raw = await this.http.request<any>({
      method: "POST",
      path: path(eventType, "/test"),
      body: { data, contract: options.contract, yaml: options.yaml },
    });
    return toContractCheck(raw)!;
  }

  /** A contract inferred from the type's recent payloads — required fields, types, enums
   * and the text field. Read it, adjust it, then `save` it. */
  async draft(
    eventType: string,
    options: { limit?: number } = {},
  ): Promise<ContractDefinition & { samples: number }> {
    return this.http.request({
      method: "POST",
      path: path(eventType, "/draft"),
      query: { limit: options.limit },
    });
  }

  /** Every event type received in the window, and the contract covering it — `mode` is
   * null for a type nothing covers. */
  async coverage(options: { since?: ContractWindow } = {}): Promise<ContractCoverage[]> {
    const raw = await this.http.request<any[]>({
      method: "GET",
      path: "/v1/contracts/coverage",
      query: { since: options.since },
    });
    return raw.map((item) => ({
      eventType: item.event_type,
      events: item.events,
      violating: item.violating,
      lastSeenAt: item.last_seen_at ?? null,
      mode: item.mode ?? null,
      version: item.version ?? null,
    }));
  }
}
