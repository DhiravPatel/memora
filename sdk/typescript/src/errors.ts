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

/** A guardrail refused the action. `check` holds every reason and its evidence. */
export class ActionDeniedError extends Error {
  readonly check: import("./types.js").ActionCheck;

  constructor(check: import("./types.js").ActionCheck) {
    super(check.summary || "The action was denied.");
    this.name = "ActionDeniedError";
    this.check = check;
  }
}

/** The action needs a person first. `approval` is the request that was filed. */
export class ApprovalRequiredError extends Error {
  readonly check: import("./types.js").ActionCheck;
  readonly approval: import("./types.js").Approval | null;

  constructor(check: import("./types.js").ActionCheck) {
    super(check.summary || "The action needs approval.");
    this.name = "ApprovalRequiredError";
    this.check = check;
    this.approval = check.approval;
  }
}

export class MemoryConfigError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "MemoryConfigError";
  }
}
