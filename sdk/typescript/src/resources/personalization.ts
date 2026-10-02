/** Personalization (§26 6.6): what your product should do differently for a customer. */

import type { HttpClient, RawResponse } from "../client.js";
import type { Personalization, PersonalizationDetails } from "../types.js";

const CACHE = 1024;

export function toPersonalization(raw: any): Personalization {
  const details = raw.details;
  return {
    customerId: raw.customer_id,
    experience: raw.experience ?? null,
    mood: raw.mood ?? null,
    preferredChannel: raw.preferred_channel ?? null,
    optOuts: raw.opt_outs ?? [],
    currentGoal: raw.current_goal ?? null,
    knownFrictions: raw.known_frictions ?? [],
    featuresUsed: raw.features_used ?? [],
    reliedOnFeatures: raw.relied_on_features ?? [],
    stage: raw.stage ?? null,
    plan: raw.plan ?? null,
    health: raw.health ?? null,
    ui: raw.ui ?? {},
    evidence: raw.evidence ?? [],
    details: details ? (details as PersonalizationDetails) : null,
    computedAt: raw.computed_at,
    changedAt: raw.changed_at,
    version: raw.version,
  };
}

export class PersonalizationResource {
  /** The last answer per customer and its ETag: a repeat read is a 304. */
  private readonly cache = new Map<string, { etag: string; value: Personalization }>();

  constructor(private readonly http: HttpClient) {}

  /** What your product should do differently for this customer: experience, mood, channel,
   * current goal, known frictions, features used, stage, plan, health and UI hints.
   *
   * Cached by ETag: while nothing changed the server answers 304 and nothing is re-sent. */
  async get(
    customerId: string,
    options: { details?: boolean; fresh?: boolean } = {},
  ): Promise<Personalization> {
    const details = options.details ?? true;
    const key = `${customerId}\u0000${details}`;
    const cached = this.cache.get(key);
    const response = await this.http.request<RawResponse<any>>({
      method: "GET",
      path: `/v1/customers/${encodeURIComponent(customerId)}/personalization`,
      query: { details, fresh: options.fresh ? true : undefined },
      headers: cached && !options.fresh ? { "If-None-Match": cached.etag } : undefined,
      raw: true,
    });
    if (response.status === 304 && cached) {
      this.remember(key, cached);
      return cached.value;
    }
    const value = toPersonalization(response.body);
    if (response.etag) this.remember(key, { etag: response.etag, value });
    return value;
  }

  /** Up to 50 customers at once — `null` for an id that names no customer. */
  async batch(
    customerIds: string[],
    options: { details?: boolean } = {},
  ): Promise<Record<string, Personalization | null>> {
    const raw = await this.http.request<any>({
      method: "POST",
      path: "/v1/personalization/batch",
      body: { customer_ids: customerIds, details: options.details ?? false },
    });
    const found: Record<string, Personalization | null> = {};
    for (const item of raw.data ?? []) {
      found[item.customer_id] = item.personalization
        ? toPersonalization(item.personalization)
        : null;
    }
    return found;
  }

  /** The rules in force: experience levels, moods and UI hints, built-ins included. */
  rules(): Promise<Record<string, unknown>> {
    return this.http.request({ method: "GET", path: "/v1/personalization/rules" });
  }

  private remember(key: string, entry: { etag: string; value: Personalization }): void {
    this.cache.delete(key);
    this.cache.set(key, entry);
    while (this.cache.size > CACHE) {
      const oldest = this.cache.keys().next().value;
      if (oldest === undefined) break;
      this.cache.delete(oldest);
    }
  }
}
