/** Error types thrown by the SDK. */

export class MemoryApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: Record<string, unknown>;
  readonly requestId: string | null;

  constructor(
    message: string,
    options: {
      status: number;
      code?: string;
      details?: Record<string, unknown>;
      requestId?: string | null;
    },
  ) {
    super(message);
    this.name = "MemoryApiError";
    this.status = options.status;
    this.code = options.code ?? "error";
    this.details = options.details ?? {};
    this.requestId = options.requestId ?? null;
  }

  /** Retrying the exact same request may succeed. */
  get isRetryable(): boolean {
    return this.status === 429 || this.status >= 500;
  }
}

export class MemoryTimeoutError extends Error {
  constructor(timeoutMs: number) {
    super(`Request timed out after ${timeoutMs}ms`);
    this.name = "MemoryTimeoutError";
  }
}

export class MemoryConfigError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "MemoryConfigError";
  }
}
