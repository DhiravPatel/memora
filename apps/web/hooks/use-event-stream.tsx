"use client";

/**
 * Live event feed over server-sent events.
 *
 * Read with `fetch` and a stream reader rather than `EventSource`, because `EventSource`
 * cannot send an `Authorization` header — the alternative would be putting the access
 * token in the URL, where it ends up in browser history and every access log in the path.
 *
 * The hook owns reconnection. The server closes a stream after ten minutes by design, so
 * a reconnect is the normal course of events rather than an error, and the backoff exists
 * for the case where the API is genuinely down.
 */

import { useEffect, useRef, useState } from "react";

import { API_URL, tokenStore } from "@/lib/api";
import type { EventRecord } from "@/lib/types";

export type StreamStatus = "connecting" | "live" | "offline";

export interface StreamedEvent extends EventRecord {
  /** Whether this frame is the event arriving or the worker finishing with it. */
  change: "created" | "processed";
}

const RECONNECT_MIN_MS = 1_000;
const RECONNECT_MAX_MS = 30_000;

export function useEventStream(
  projectId: string | null,
  onEvent: (event: StreamedEvent) => void,
  options: { enabled?: boolean; sinceSeconds?: number } = {},
) {
  const { enabled = true, sinceSeconds = 30 } = options;
  const [status, setStatus] = useState<StreamStatus>("connecting");
  const [lastEventAt, setLastEventAt] = useState<Date | null>(null);
  // Held in a ref so a changing callback does not tear down the connection.
  const handler = useRef(onEvent);
  handler.current = onEvent;

  useEffect(() => {
    if (!projectId || !enabled) {
      setStatus("offline");
      return;
    }

    const controller = new AbortController();
    let closed = false;
    let attempt = 0;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;

    async function connect() {
      setStatus(attempt === 0 ? "connecting" : "offline");
      try {
        const response = await fetch(
          `${API_URL}/v1/projects/${projectId}/events/stream?since_seconds=${sinceSeconds}`,
          {
            headers: {
              Accept: "text/event-stream",
              ...(tokenStore.access ? { Authorization: `Bearer ${tokenStore.access}` } : {}),
            },
            signal: controller.signal,
          },
        );

        if (!response.ok || !response.body) {
          throw new Error(`stream failed (${response.status})`);
        }

        setStatus("live");
        attempt = 0;

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        while (!closed) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });

          // Frames are separated by a blank line; anything after the last one is a
          // partial frame and stays in the buffer until the rest arrives.
          const frames = buffer.split("\n\n");
          buffer = frames.pop() ?? "";

          for (const frame of frames) {
            const parsed = parseFrame(frame);
            if (!parsed) continue;
            if (parsed.event === "event") {
              handler.current(parsed.data as StreamedEvent);
              setLastEventAt(new Date());
            } else if (parsed.event === "heartbeat") {
              setLastEventAt((current) => current);
            }
          }
        }
      } catch (error) {
        if (controller.signal.aborted || closed) return;
        setStatus("offline");
      }

      if (closed) return;
      // Either the server closed the stream on schedule or something broke; both are
      // handled by reconnecting, with backoff for the second case.
      attempt += 1;
      const delay = Math.min(RECONNECT_MAX_MS, RECONNECT_MIN_MS * 2 ** (attempt - 1));
      retryTimer = setTimeout(connect, attempt === 1 ? 0 : delay);
    }

    void connect();

    return () => {
      closed = true;
      controller.abort();
      if (retryTimer) clearTimeout(retryTimer);
    };
  }, [projectId, enabled, sinceSeconds]);

  return { status, lastEventAt };
}

function parseFrame(frame: string): { event: string; data: unknown } | null {
  let event = "message";
  const data: string[] = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data.push(line.slice(5).trim());
  }
  if (!data.length) return null;
  try {
    return { event, data: JSON.parse(data.join("\n")) };
  } catch {
    return null;
  }
}
