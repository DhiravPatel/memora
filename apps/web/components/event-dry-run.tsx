"use client";

import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

import { ExplanationPanel } from "@/components/event-explanation";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label } from "@/components/ui/input";
import { api } from "@/lib/api";
import type { EventExplanation } from "@/lib/types";

const EXAMPLE = `{
  "message": "The Shopify sync keeps failing during checkout."
}`;

/** Send an event without sending it.
 *
 * The question this answers — "why did my event not produce a memory?" — is normally asked
 * after the fact, from a position of having already sent a thousand of them. Asking it
 * first costs nothing, because the preview writes nothing.
 */
export function EventDryRun({ projectId }: { projectId: string | null }) {
  const [customerId, setCustomerId] = useState("");
  const [eventType, setEventType] = useState("support_message");
  const [payload, setPayload] = useState(EXAMPLE);
  const [jsonError, setJsonError] = useState<string | null>(null);

  const run = useMutation({
    mutationFn: (data: Record<string, unknown>) =>
      api<EventExplanation>(`/v1/projects/${projectId}/events/preview`, {
        method: "POST",
        body: { customer_id: customerId, event_type: eventType, data },
      }),
  });

  function submit(event: React.FormEvent) {
    event.preventDefault();
    let data: Record<string, unknown>;
    try {
      data = JSON.parse(payload);
    } catch (error) {
      // Caught here rather than sent, so a stray comma reads as a stray comma instead of
      // a 422 from the other end of the network.
      setJsonError((error as Error).message);
      return;
    }
    setJsonError(null);
    run.mutate(data);
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Dry run</CardTitle>
        <CardDescription>
          What this event would do, decided by the real pipeline and then stopped before it writes.
          Nothing is stored and no memory changes.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <form onSubmit={submit} className="space-y-3">
          <div className="flex flex-wrap gap-3">
            <div className="min-w-[14rem] flex-1">
              <Label htmlFor="dry-customer">Customer id</Label>
              <Input
                id="dry-customer"
                required
                placeholder="cus_123"
                value={customerId}
                onChange={(event) => setCustomerId(event.target.value)}
              />
            </div>
            <div className="min-w-[14rem] flex-1">
              <Label htmlFor="dry-type">Event type</Label>
              <Input
                id="dry-type"
                required
                value={eventType}
                onChange={(event) => setEventType(event.target.value)}
              />
            </div>
          </div>
          <div>
            <Label htmlFor="dry-payload">Payload</Label>
            <textarea
              id="dry-payload"
              rows={6}
              spellCheck={false}
              value={payload}
              onChange={(event) => setPayload(event.target.value)}
              className="w-full border border-border bg-surface px-3 py-2 font-mono text-[11px] leading-relaxed focus:border-accent focus:outline-none"
            />
            {jsonError && <p className="mt-1 font-mono text-[11px] text-danger">{jsonError}</p>}
          </div>
          <Button type="submit" size="sm" loading={run.isPending} disabled={!projectId}>
            Run without sending
          </Button>
        </form>

        {run.error && (
          <p className="border-l-2 border-danger bg-surface-2 px-3 py-2 font-mono text-[11px] text-danger">
            {run.error instanceof Error ? run.error.message : "The preview failed."}
          </p>
        )}
        {run.data && <ExplanationPanel explanation={run.data} />}
      </CardContent>
    </Card>
  );
}
