"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { useGuardrailCatalog } from "@/components/agents/shared";
import { Badge, MemoryTypeBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label, Textarea } from "@/components/ui/input";
import { EmptyState, ErrorState, LoadingRow } from "@/components/ui/states";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { AgentProfile } from "@/lib/types";

const MEMORY_TYPES = [
  "fact",
  "preference",
  "problem",
  "goal",
  "behavior",
  "relationship",
  "subscription",
  "feedback",
  "intent",
  "summary",
];

interface Draft {
  id?: string;
  name: string;
  description: string;
  readable_types: string[];
  can_read_restricted: boolean;
  allowed_actions: string;
  denied_actions: string;
}

const EMPTY: Draft = {
  name: "",
  description: "",
  readable_types: [],
  can_read_restricted: false,
  allowed_actions: "",
  denied_actions: "",
};

const split = (value: string) =>
  value
    .split(/[\s,]+/)
    .map((item) => item.trim())
    .filter(Boolean);

/** What each agent is for. A profile only ever narrows the key bound to it. */
export function ProfilesManager({
  projectId,
  canEdit,
}: {
  projectId: string | null;
  canEdit: boolean;
}) {
  const client = useQueryClient();
  const catalog = useGuardrailCatalog(projectId);
  const [draft, setDraft] = useState<Draft | null>(null);

  const profiles = useQuery({
    queryKey: ["agent-profiles", projectId],
    queryFn: () => api<AgentProfile[]>(`/v1/projects/${projectId}/agent/profiles`),
    enabled: Boolean(projectId),
  });

  const save = useMutation({
    mutationFn: (value: Draft) => {
      const body = {
        description: value.description || null,
        readable_types: value.readable_types,
        can_read_restricted: value.can_read_restricted,
        allowed_actions: split(value.allowed_actions),
        denied_actions: split(value.denied_actions),
      };
      return value.id
        ? api<AgentProfile>(`/v1/projects/${projectId}/agent/profiles/${value.id}`, {
            method: "PATCH",
            body,
          })
        : api<AgentProfile>(`/v1/projects/${projectId}/agent/profiles`, {
            method: "POST",
            body: { ...body, name: value.name },
          });
    },
    onSuccess: () => {
      setDraft(null);
      client.invalidateQueries({ queryKey: ["agent-profiles", projectId] });
    },
  });

  const remove = useMutation({
    mutationFn: (id: string) =>
      api(`/v1/projects/${projectId}/agent/profiles/${id}`, { method: "DELETE" }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["agent-profiles", projectId] }),
  });

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="max-w-2xl text-xs leading-relaxed text-muted-foreground">
          Bind an API key to a profile on the Keys page. The key then reads only these memory types
          — through every endpoint, answers and context included — needs the profile&apos;s consent
          as well as its own scope for restricted memory, and is held to these actions by every
          guardrail check.
        </p>
        {canEdit && !draft && (
          <Button size="sm" onClick={() => setDraft({ ...EMPTY })}>
            New profile
          </Button>
        )}
      </div>

      {(save.error || remove.error) && <ErrorState error={save.error ?? remove.error} />}

      {draft && (
        <Card>
          <CardHeader>
            <CardTitle>{draft.id ? `Edit ${draft.name}` : "New agent profile"}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid gap-4 md:grid-cols-2">
              <div>
                <Label htmlFor="profile-name">Name</Label>
                <Input
                  id="profile-name"
                  value={draft.name}
                  disabled={Boolean(draft.id)}
                  placeholder="support-agent"
                  onChange={(event) => setDraft({ ...draft, name: event.target.value })}
                />
              </div>
              <div>
                <Label htmlFor="profile-description">What it does</Label>
                <Input
                  id="profile-description"
                  value={draft.description}
                  placeholder="Answers support tickets"
                  onChange={(event) => setDraft({ ...draft, description: event.target.value })}
                />
              </div>
            </div>

            <div>
              <Label>Memory it may read — none selected means all of it</Label>
              <div className="mt-1 flex flex-wrap gap-1.5">
                {MEMORY_TYPES.map((type) => {
                  const active = draft.readable_types.includes(type);
                  return (
                    <button
                      key={type}
                      type="button"
                      onClick={() =>
                        setDraft({
                          ...draft,
                          readable_types: active
                            ? draft.readable_types.filter((item) => item !== type)
                            : [...draft.readable_types, type],
                        })
                      }
                      className={cn(
                        "border px-2 py-1 font-mono text-[10px] uppercase tracking-label",
                        active
                          ? "border-accent bg-accent/10 text-accent"
                          : "border-border text-muted-foreground",
                      )}
                    >
                      {type}
                    </button>
                  );
                })}
              </div>
            </div>

            <label className="flex items-center gap-2 text-xs">
              <input
                type="checkbox"
                checked={draft.can_read_restricted}
                onChange={(event) =>
                  setDraft({ ...draft, can_read_restricted: event.target.checked })
                }
              />
              May read restricted memories — only with keys that also hold memory:restricted
            </label>

            <div className="grid gap-4 md:grid-cols-2">
              <div>
                <Label htmlFor="profile-allowed">Only these actions (empty: any not denied)</Label>
                <Textarea
                  id="profile-allowed"
                  rows={2}
                  value={draft.allowed_actions}
                  placeholder="contact_customer, create_ticket"
                  onChange={(event) => setDraft({ ...draft, allowed_actions: event.target.value })}
                />
              </div>
              <div>
                <Label htmlFor="profile-denied">Never these actions</Label>
                <Textarea
                  id="profile-denied"
                  rows={2}
                  value={draft.denied_actions}
                  placeholder="offer_discount, process_refund"
                  onChange={(event) => setDraft({ ...draft, denied_actions: event.target.value })}
                />
              </div>
            </div>
            {catalog.data && (
              <p className="label leading-relaxed">
                Recognised by the built-in rules:{" "}
                {catalog.data.actions.map((item) => item.action).join(", ")}. Any other name works
                too — your project rules can match it.
              </p>
            )}

            <div className="flex gap-2">
              <Button
                size="sm"
                loading={save.isPending}
                onClick={() => save.mutate(draft)}
                disabled={!draft.name.trim()}
              >
                Save
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setDraft(null)}>
                Cancel
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      <Card>
        {profiles.isLoading && <LoadingRow />}
        {profiles.error && <ErrorState error={profiles.error} />}
        {profiles.data?.length === 0 && (
          <EmptyState
            title="No agent profiles"
            description="A support agent rarely needs sales notes; a sales agent must not issue refunds. Profiles say so, and every read and check enforces it."
          />
        )}
        <div className="divide-y divide-border">
          {profiles.data?.map((profile) => (
            <div
              key={profile.id}
              className="flex flex-wrap items-start justify-between gap-4 px-5 py-4"
            >
              <div className="min-w-0 space-y-2">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-sm text-foreground">{profile.name}</span>
                  <Badge>
                    {profile.keys} key{profile.keys === 1 ? "" : "s"}
                  </Badge>
                  {profile.can_read_restricted && (
                    <Badge className="border-danger/40 bg-danger/10 text-danger">
                      restricted ok
                    </Badge>
                  )}
                </div>
                {profile.description && (
                  <p className="text-xs text-muted-foreground">{profile.description}</p>
                )}
                <div className="flex flex-wrap items-center gap-1">
                  <span className="label mr-1">reads</span>
                  {profile.readable_types.length ? (
                    profile.readable_types.map((type) => <MemoryTypeBadge key={type} type={type} />)
                  ) : (
                    <span className="label">every type</span>
                  )}
                </div>
                <p className="label">
                  {profile.allowed_actions.length
                    ? `only: ${profile.allowed_actions.join(", ")}`
                    : "any action"}
                  {profile.denied_actions.length
                    ? ` · never: ${profile.denied_actions.join(", ")}`
                    : ""}
                </p>
              </div>
              {canEdit && (
                <div className="flex gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() =>
                      setDraft({
                        id: profile.id,
                        name: profile.name,
                        description: profile.description ?? "",
                        readable_types: profile.readable_types,
                        can_read_restricted: profile.can_read_restricted,
                        allowed_actions: profile.allowed_actions.join(", "),
                        denied_actions: profile.denied_actions.join(", "),
                      })
                    }
                  >
                    Edit
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={profile.keys > 0}
                    title={profile.keys > 0 ? "Rebind or revoke its keys first" : "Delete"}
                    onClick={() => remove.mutate(profile.id)}
                  >
                    Delete
                  </Button>
                </div>
              )}
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
