"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { DecisionBadge, ReasonList, useGuardrailCatalog } from "@/components/agents/shared";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { ErrorState } from "@/components/ui/states";
import { api } from "@/lib/api";
import type { AgentCheck, AgentProfile } from "@/lib/types";

/** "What would happen if…?" — a guardrail check that is never recorded.
 *
 * For writing policy: pick a customer, an action and, optionally, the profile an agent would
 * act as, and see the verdict and every reason. Nothing is filed; no approval is requested.
 */
export function PolicySimulator({
  projectId,
  customerId,
}: {
  projectId: string | null;
  customerId?: string;
}) {
  const catalog = useGuardrailCatalog(projectId);
  const [customer, setCustomer] = useState(customerId ?? "");
  const [action, setAction] = useState("offer_upgrade");
  const [request, setRequest] = useState('{\n  "channel": "email"\n}');
  const [profileId, setProfileId] = useState("");
  const [parseError, setParseError] = useState<string | null>(null);

  const profiles = useQuery({
    queryKey: ["agent-profiles", projectId],
    queryFn: () => api<AgentProfile[]>(`/v1/projects/${projectId}/agent/profiles`),
    enabled: Boolean(projectId),
  });

  const simulate = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      api<AgentCheck>(`/v1/projects/${projectId}/agent/simulate`, { method: "POST", body }),
  });

  function run() {
    let parsed: Record<string, unknown> = {};
    try {
      parsed = request.trim() ? JSON.parse(request) : {};
      if (typeof parsed !== "object" || Array.isArray(parsed) || parsed === null)
        throw new Error("not an object");
      setParseError(null);
    } catch {
      setParseError('The request must be a JSON object, e.g. {"amount": 50}.');
      return;
    }
    simulate.mutate({
      customer_id: customer.trim(),
      action: action.trim(),
      request: parsed,
      profile_id: profileId || undefined,
    });
  }

  const result = simulate.data;
  return (
    <div className="grid gap-4 lg:grid-cols-[22rem_1fr]">
      <Card>
        <CardHeader>
          <CardTitle>Try a check</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {!customerId && (
            <div>
              <Label htmlFor="sim-customer">Customer</Label>
              <Input
                id="sim-customer"
                value={customer}
                placeholder="cus_123"
                onChange={(event) => setCustomer(event.target.value)}
              />
            </div>
          )}
          <div>
            <Label htmlFor="sim-action">Action</Label>
            <Input
              id="sim-action"
              list="sim-actions"
              value={action}
              onChange={(event) => setAction(event.target.value)}
            />
            <datalist id="sim-actions">
              {catalog.data?.actions.map((item) => (
                <option key={item.action} value={item.action}>
                  {item.family}
                </option>
              ))}
            </datalist>
          </div>
          <div>
            <Label htmlFor="sim-request">Request — what the rules read</Label>
            <Textarea
              id="sim-request"
              rows={4}
              value={request}
              onChange={(event) => setRequest(event.target.value)}
              className="font-mono text-[11px]"
            />
          </div>
          <div>
            <Label htmlFor="sim-profile">As agent profile</Label>
            <Select
              id="sim-profile"
              value={profileId}
              onChange={(event) => setProfileId(event.target.value)}
            >
              <option value="">No profile</option>
              {profiles.data?.map((profile) => (
                <option key={profile.id} value={profile.id}>
                  {profile.name}
                </option>
              ))}
            </Select>
          </div>
          <Button
            onClick={run}
            loading={simulate.isPending}
            disabled={!customer.trim() || !action.trim()}
          >
            Check
          </Button>
          {parseError && <p className="text-xs text-danger">{parseError}</p>}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Verdict</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {simulate.error && <ErrorState error={simulate.error} />}
          {!result && !simulate.error && (
            <p className="label">
              Not recorded and no approval filed — this shows what an agent would be told.
            </p>
          )}
          {result && (
            <>
              <div className="flex flex-wrap items-center gap-2">
                <DecisionBadge decision={result.decision} />
                <span className="font-mono text-xs">{result.action}</span>
                {result.profile && <span className="label">as {result.profile}</span>}
              </div>
              <p className="text-sm leading-relaxed">{result.summary}</p>
              <ReasonList reasons={result.reasons} />
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
