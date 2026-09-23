/** HTTP transport: retries, timeouts, and error mapping. */

import { MemoryApiError, MemoryConfigError, MemoryTimeoutError } from "./errors.js";
import type { ClientOptions } from "./types.js";

const DEFAULT_BASE_URL = "https://api.aimemorylayer.com";
const RETRYABLE_STATUS = new Set([408, 409, 425, 429, 500, 502, 503, 504]);

export interface RequestOptions {
  method: "GET" | "POST" | "PATCH" | "DELETE";
  path: string;
  body?: unknown;
  query?: Record<string, string | number | boolean | undefined | null>;
  signal?: AbortSignal;
  idempotencyKey?: string;
}

export class HttpClient {
  private readonly apiKey: string;
  private readonly baseUrl: string;
  private readonly timeoutMs: number;
  private readonly maxRetries: number;
  private readonly headers: Record<string, string>;
  private readonly fetchImpl: typeof globalThis.fetch;

  constructor(options: ClientOptions) {
    if (!options.apiKey) {
      throw new MemoryConfigError("apiKey is required.");
    }
    this.apiKey = options.apiKey;
    this.baseUrl = (options.baseUrl ?? DEFAULT_BASE_URL).replace(/\/+$/, "");
    this.timeoutMs = options.timeoutMs ?? 30_000;
    this.maxRetries = Math.max(0, options.maxRetries ?? 2);
    this.headers = options.headers ?? {};
    const fetchImpl = options.fetch ?? globalThis.fetch;
    if (!fetchImpl) {
      throw new MemoryConfigError(
        "No fetch implementation available. Pass `fetch` explicitly on Node < 18.",
      );
    }
    this.fetchImpl = fetchImpl.bind(globalThis);
  }

  async request<T>(options: RequestOptions): Promise<T> {
    const url = new URL(this.baseUrl + options.path);
    for (const [key, value] of Object.entries(options.query ?? {})) {
      if (value !== undefined && value !== null) url.searchParams.set(key, String(value));
    }

    let lastError: unknown;
    for (let attempt = 0; attempt <= this.maxRetries; attempt += 1) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), this.timeoutMs);
      if (options.signal) {
        options.signal.addEventListener("abort", () => controller.abort(), { once: true });
      }

      try {
        const response = await this.fetchImpl(url.toString(), {
          method: options.method,
          headers: {
            "Content-Type": "application/json",
            "X-API-Key": this.apiKey,
            ...(options.idempotencyKey ? { "Idempotency-Key": options.idempotencyKey } : {}),
            ...this.headers,
          },
          body: options.body === undefined ? undefined : JSON.stringify(options.body),
          signal: controller.signal,
        });

        if (response.ok) {
          if (response.status === 204) return undefined as T;
          return (await response.json()) as T;
        }

        const payload = await safeJson(response);
        const error = new MemoryApiError(
          payload?.error?.message ?? `Request failed with status ${response.status}`,
          {
            status: response.status,
            code: payload?.error?.code,
            details: payload?.error?.details,
            requestId: response.headers.get("x-request-id"),
          },
        );
        if (!RETRYABLE_STATUS.has(response.status) || attempt === this.maxRetries) throw error;
        lastError = error;
      } catch (error) {
        if (error instanceof MemoryApiError && !error.isRetryable) throw error;
        if (controller.signal.aborted && !options.signal?.aborted) {
          lastError = new MemoryTimeoutError(this.timeoutMs);
        } else {
          lastError = error;
        }
        if (attempt === this.maxRetries) break;
      } finally {
        clearTimeout(timer);
      }

      // Exponential backoff with jitter.
      const delay = Math.min(4000, 250 * 2 ** attempt) * (0.5 + Math.random() / 2);
      await new Promise((resolve) => setTimeout(resolve, delay));
    }

    throw lastError instanceof Error
      ? lastError
      : new MemoryApiError("Request failed.", { status: 0 });
  }
}

async function safeJson(response: Response): Promise<any> {
  try {
    return await response.json();
  } catch {
    return null;
  }
}
