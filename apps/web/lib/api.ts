"use client";

/** Browser API client. Talks to the dashboard (JWT) endpoints only. */

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const ACCESS_TOKEN_KEY = "aiml.access_token";
const REFRESH_TOKEN_KEY = "aiml.refresh_token";
const PROJECT_KEY = "aiml.project_id";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code = "error",
  ) {
    super(message);
  }
}

// Who wants to know when the tokens change: the session provider lives in the root layout,
// which does not re-render on a client-side navigation — without this, signing in left the
// dashboard with no user and no projects until a full reload.
const tokenListeners = new Set<() => void>();

export const tokenStore = {
  get access() {
    return typeof window === "undefined" ? null : localStorage.getItem(ACCESS_TOKEN_KEY);
  },
  get refresh() {
    return typeof window === "undefined" ? null : localStorage.getItem(REFRESH_TOKEN_KEY);
  },
  set(tokens: { access_token: string; refresh_token: string }) {
    localStorage.setItem(ACCESS_TOKEN_KEY, tokens.access_token);
    localStorage.setItem(REFRESH_TOKEN_KEY, tokens.refresh_token);
    tokenListeners.forEach((listener) => listener());
  },
  clear() {
    localStorage.removeItem(ACCESS_TOKEN_KEY);
    localStorage.removeItem(REFRESH_TOKEN_KEY);
    tokenListeners.forEach((listener) => listener());
  },
  /** Called whenever the tokens are set or cleared. Returns the unsubscribe. */
  subscribe(listener: () => void): () => void {
    tokenListeners.add(listener);
    return () => {
      tokenListeners.delete(listener);
    };
  },
};

export const projectStore = {
  get(): string | null {
    return typeof window === "undefined" ? null : localStorage.getItem(PROJECT_KEY);
  },
  set(projectId: string) {
    localStorage.setItem(PROJECT_KEY, projectId);
  },
  clear() {
    localStorage.removeItem(PROJECT_KEY);
  },
};

interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  query?: Record<string, string | number | boolean | undefined | null>;
  auth?: boolean;
  retryOnUnauthorized?: boolean;
}

export async function api<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, query, auth = true, retryOnUnauthorized = true } = options;
  const url = new URL(API_URL + path);
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined && value !== null && value !== "") {
      url.searchParams.set(key, String(value));
    }
  }

  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (auth && tokenStore.access) headers.Authorization = `Bearer ${tokenStore.access}`;

  const response = await fetch(url.toString(), {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  if (response.status === 401 && auth && retryOnUnauthorized && tokenStore.refresh) {
    const refreshed = await refreshSession();
    if (refreshed) return api<T>(path, { ...options, retryOnUnauthorized: false });
  }

  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new ApiError(
      payload?.error?.message ?? `Request failed (${response.status})`,
      response.status,
      payload?.error?.code,
    );
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

async function refreshSession(): Promise<boolean> {
  try {
    const tokens = await api<{ access_token: string; refresh_token: string }>(
      "/v1/auth/refresh",
      {
        method: "POST",
        body: { refresh_token: tokenStore.refresh },
        auth: false,
        retryOnUnauthorized: false,
      },
    );
    tokenStore.set(tokens);
    return true;
  } catch {
    tokenStore.clear();
    return false;
  }
}

export { API_URL };
