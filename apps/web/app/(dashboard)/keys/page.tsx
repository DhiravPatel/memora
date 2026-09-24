"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label, Select } from "@/components/ui/input";
import { EmptyState, ErrorState, LoadingRow, PageHeader } from "@/components/ui/states";
import { Table, TD, TH, THead, TR } from "@/components/ui/table";
import { useSession } from "@/hooks/use-session";
import { api } from "@/lib/api";
import { formatDate, formatRelative } from "@/lib/format";
import { cn, SCOPE_COLORS } from "@/lib/utils";
import type { AgentProfile, ApiKey, ApiKeyWithSecret, ScopeInfo } from "@/lib/types";

const EXPIRY_OPTIONS = [
  { label: "Never expires", value: "" },
  { label: "30 days", value: "30" },
  { label: "90 days", value: "90" },
  { label: "1 year", value: "365" },
];

export default function ApiKeysPage() {
  const { projectId } = useSession();
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [expiry, setExpiry] = useState("");
  const [selected, setSelected] = useState<string[]>([
    "events:write",
    "memory:read",
    "customers:read",
  ]);
  const [revealed, setRevealed] = useState<ApiKeyWithSecret | null>(null);
  const [showRevoked, setShowRevoked] = useState(false);
  const [profileId, setProfileId] = useState("");

  const profiles = useQuery({
    queryKey: ["agent-profiles", projectId],
    queryFn: () => api<AgentProfile[]>(`/v1/projects/${projectId}/agent/profiles`),
    enabled: Boolean(projectId),
  });
  const profileName = (id: string | null) =>
    profiles.data?.find((profile) => profile.id === id)?.name ?? id;

  const keys = useQuery({
    queryKey: ["api-keys", projectId, showRevoked],
    queryFn: () =>
      api<ApiKey[]>(`/v1/projects/${projectId}/api-keys`, {
        query: { include_revoked: showRevoked },
      }),
    enabled: Boolean(projectId),
  });

  const scopes = useQuery({
    queryKey: ["api-key-scopes", projectId],
    queryFn: () => api<ScopeInfo[]>(`/v1/projects/${projectId}/api-keys/scopes`),
    enabled: Boolean(projectId),
  });

  const create = useMutation({
    mutationFn: () =>
      api<ApiKeyWithSecret>(`/v1/projects/${projectId}/api-keys`, {
        method: "POST",
        body: {
          name,
          scopes: selected,
          expires_in_days: expiry ? Number(expiry) : undefined,
          agent_profile_id: profileId || undefined,
        },
      }),
    onSuccess: (created) => {
      setRevealed(created);
      setName("");
      queryClient.invalidateQueries({ queryKey: ["api-keys"] });
    },
  });

  // Rebinding a live key changes what it can read on its very next request.
  const bind = useMutation({
    mutationFn: ({ keyId, agentProfileId }: { keyId: string; agentProfileId: string }) =>
      api<ApiKey>(`/v1/projects/${projectId}/api-keys/${keyId}`, {
        method: "PATCH",
        body: { agent_profile_id: agentProfileId || null },
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["api-keys"] });
      queryClient.invalidateQueries({ queryKey: ["agent-profiles", projectId] });
    },
  });

  const revoke = useMutation({
    mutationFn: (keyId: string) =>
      api(`/v1/projects/${projectId}/api-keys/${keyId}`, { method: "DELETE" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["api-keys"] }),
  });

  function toggleScope(scope: string) {
    setSelected((current) =>
      current.includes(scope) ? current.filter((item) => item !== scope) : [...current, scope],
    );
  }

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="System"
        title="API keys"
        description="Least privilege by default: an ingestion key on a web server should not be able to read a customer's memories."
        actions={
          <Button variant="outline" size="sm" onClick={() => setShowRevoked((value) => !value)}>
            {showRevoked ? "Hide revoked" : "Show revoked"}
          </Button>
        }
      />

      {revealed && (
        <Card className="border-accent/60 bg-accent-soft shadow-[0_0_0_4px_hsl(var(--accent)/0.08)]">
          <CardHeader>
            <CardTitle className="text-accent">{revealed.name}</CardTitle>
            <CardDescription>
              Shown once. Only an HMAC digest is stored, so this value cannot be recovered.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <code className="block break-all border border-border bg-surface-2 p-3 font-mono text-xs text-accent">
              {revealed.api_key}
            </code>
            <div className="flex gap-2">
              <Button size="sm" onClick={() => navigator.clipboard.writeText(revealed.api_key)}>
                Copy
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setRevealed(null)}>
                Done
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Create a key</CardTitle>
          <CardDescription>
            Name it for where it runs, and grant only what it needs.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <Label htmlFor="key-name">Name</Label>
              <Input
                id="key-name"
                placeholder="WEB SERVER — INGESTION"
                className="uppercase tracking-label"
                value={name}
                onChange={(event) => setName(event.target.value)}
              />
            </div>
            <div>
              <Label htmlFor="key-expiry">Expiry</Label>
              <Select
                id="key-expiry"
                value={expiry}
                onChange={(event) => setExpiry(event.target.value)}
              >
                {EXPIRY_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </Select>
            </div>
            <div className="sm:col-span-2">
              <Label htmlFor="key-profile">Acts as agent profile</Label>
              <Select
                id="key-profile"
                value={profileId}
                onChange={(event) => setProfileId(event.target.value)}
              >
                <option value="">No profile — the scopes alone decide</option>
                {profiles.data?.map((profile) => (
                  <option key={profile.id} value={profile.id}>
                    {profile.name}
                    {profile.readable_types.length
                      ? ` — reads ${profile.readable_types.join(", ")}`
                      : " — reads everything"}
                  </option>
                ))}
              </Select>
              <p className="label mt-1">
                A profile narrows the key: it reads only the profile&apos;s memory types and is held
                to its actions. Manage profiles on the Agents page.
              </p>
            </div>
          </div>

          <div>
            <Label>Scopes</Label>
            <div className="grid gap-px border border-border bg-border sm:grid-cols-2">
              {scopes.data?.map((scope) => (
                <button
                  key={scope.scope}
                  onClick={() => toggleScope(scope.scope)}
                  className={cn(
                    "flex items-start gap-3 bg-surface p-3 text-left transition-colors hover:bg-surface-2",
                    selected.includes(scope.scope) && "bg-surface-2",
                  )}
                >
                  <span
                    className={cn(
                      "mt-0.5 h-3.5 w-3.5 shrink-0 border",
                      selected.includes(scope.scope)
                        ? "border-accent bg-accent"
                        : "border-border-strong bg-surface",
                    )}
                  />
                  <span>
                    <span className="block font-mono text-[11px] uppercase tracking-label">
                      {scope.scope}
                    </span>
                    <span className="mt-0.5 block text-[11px] text-muted-foreground">
                      {scope.description}
                    </span>
                  </span>
                </button>
              ))}
            </div>
          </div>

          <div className="flex items-center gap-3">
            <Button
              onClick={() => create.mutate()}
              loading={create.isPending}
              disabled={!name.trim() || selected.length === 0}
            >
              Create key
            </Button>
            {create.error && (
              <p className="font-mono text-[11px] text-danger">
                {create.error instanceof Error ? create.error.message : "Could not create key."}
              </p>
            )}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Keys</CardTitle>
        </CardHeader>
        {keys.isLoading && <LoadingRow />}
        {keys.error && <ErrorState error={keys.error} />}
        {keys.data?.length === 0 && <EmptyState title="No keys" />}
        {!!keys.data?.length && (
          <Table className="min-w-[1060px]">
            <THead>
              <TR>
                <TH>Name</TH>
                <TH className="w-44">Prefix</TH>
                <TH>Scopes</TH>
                <TH className="w-44">Agent profile</TH>
                <TH className="w-28">Used</TH>
                <TH className="w-32">Last used</TH>
                <TH className="w-28">Expires</TH>
                <TH className="w-24 text-right" />
              </TR>
            </THead>
            <tbody>
              {keys.data.map((key) => (
                <TR key={key.id} className={cn(!key.is_active && "opacity-50")}>
                  <TD>
                    <p className="text-sm font-semibold">{key.name}</p>
                    <p className="label mt-0.5">created {formatRelative(key.created_at)}</p>
                  </TD>
                  <TD className="font-mono text-[11px]">{key.key_prefix}…</TD>
                  <TD>
                    <div className="flex flex-wrap gap-1">
                      {key.scopes.map((scope) => (
                        <Badge key={scope} className={SCOPE_COLORS[scope] ?? ""}>
                          {scope}
                        </Badge>
                      ))}
                    </div>
                  </TD>
                  <TD>
                    {key.is_active ? (
                      <Select
                        value={key.agent_profile_id ?? ""}
                        onChange={(event) =>
                          bind.mutate({ keyId: key.id, agentProfileId: event.target.value })
                        }
                        className="h-7 text-[11px]"
                        title="The agent profile this key acts as"
                      >
                        <option value="">none</option>
                        {profiles.data?.map((profile) => (
                          <option key={profile.id} value={profile.id}>
                            {profile.name}
                          </option>
                        ))}
                      </Select>
                    ) : (
                      <span className="label">
                        {key.agent_profile_id ? profileName(key.agent_profile_id) : "—"}
                      </span>
                    )}
                  </TD>
                  <TD className="numeric text-[11px] text-muted-foreground">{key.use_count}</TD>
                  <TD className="label whitespace-nowrap">{formatRelative(key.last_used_at)}</TD>
                  <TD className="label whitespace-nowrap">
                    {key.revoked_at
                      ? "revoked"
                      : key.expires_at
                        ? formatDate(key.expires_at)
                        : "never"}
                  </TD>
                  <TD className="text-right">
                    {key.is_active && (
                      <Button
                        size="sm"
                        variant="ghost"
                        loading={revoke.isPending && revoke.variables === key.id}
                        onClick={() => revoke.mutate(key.id)}
                      >
                        Revoke
                      </Button>
                    )}
                  </TD>
                </TR>
              ))}
            </tbody>
          </Table>
        )}
        {revoke.error && <ErrorState error={revoke.error} />}
        {bind.error && <ErrorState error={bind.error} />}
      </Card>
    </div>
  );
}
