"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { LifecycleEditor } from "@/components/settings/lifecycle-editor";
import { Button } from "@/components/ui/button";
import { Input, Label } from "@/components/ui/input";
import { useSession } from "@/hooks/use-session";
import { api } from "@/lib/api";
import type { LifecycleDefinition, TrackSettings, TrackTemplate } from "@/lib/types";

/** Lifecycle tracks beside the primary machine (§26 4.2).
 *
 * Each track is written exactly like the lifecycle — states, an initial state, ordered
 * transitions in the condition language — so the same editor edits it. Templates are the
 * shipped engagement and commercial machines; adding one copies it, and from then on it is
 * the project's to change.
 */
export function TracksEditor({
  value,
  onChange,
}: {
  value: TrackSettings;
  onChange: (value: TrackSettings) => void;
}) {
  const { projectId } = useSession();
  const [open, setOpen] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const templates = useQuery({
    queryKey: ["lifecycle-templates", projectId],
    queryFn: () => api<TrackTemplate[]>(`/v1/projects/${projectId}/lifecycle/templates`),
    enabled: Boolean(projectId),
    staleTime: 60 * 60 * 1000,
  });
  const tracks = value ?? {};
  const names = Object.keys(tracks);

  function update(name: string, patch: Partial<TrackSettings[string]>) {
    onChange({ ...tracks, [name]: { ...tracks[name], ...patch } });
  }

  function remove(name: string) {
    const next = { ...tracks };
    delete next[name];
    onChange(next);
    if (open === name) setOpen(null);
  }

  function addTemplate(template: TrackTemplate) {
    onChange({
      ...tracks,
      [template.name]: {
        label: template.label,
        description: template.description,
        enabled: true,
        states: template.states,
        initial: template.initial,
        transitions: template.transitions,
      },
    });
    setOpen(template.name);
  }

  function addEmpty() {
    const name = newName
      .trim()
      .toLowerCase()
      .replace(/[\s-]+/g, "_");
    if (!/^[a-z][a-z0-9_]{0,39}$/.test(name) || name === "lifecycle" || tracks[name]) return;
    onChange({
      ...tracks,
      [name]: {
        label: name.replace(/_/g, " "),
        enabled: true,
        states: ["start"],
        initial: "start",
        transitions: [],
      },
    });
    setNewName("");
    setOpen(name);
  }

  const missing = (templates.data ?? []).filter((template) => !tracks[template.name]);
  return (
    <div className="w-full max-w-4xl space-y-4">
      {names.length === 0 && (
        <p className="text-xs text-muted-foreground">
          No tracks beside the primary lifecycle. Add one from a template, or start from scratch.
        </p>
      )}
      {names.map((name) => {
        const track = tracks[name];
        return (
          <div key={name} className="border border-border bg-surface-2 p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex flex-wrap items-center gap-3">
                <span className="font-mono text-[11px] text-foreground">{name}</span>
                <Input
                  value={track.label ?? ""}
                  onChange={(event) => update(name, { label: event.target.value })}
                  className="h-7 w-40 text-[11px]"
                  aria-label={`Label for ${name}`}
                />
                <span className="label">
                  {track.states?.length ?? 0} states · {track.transitions?.length ?? 0} transitions
                  {track.enabled === false ? " · off" : ""}
                </span>
              </div>
              <div className="flex gap-1">
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => setOpen(open === name ? null : name)}
                >
                  {open === name ? "Close" : "Edit"}
                </Button>
                <Button size="sm" variant="ghost" onClick={() => remove(name)} title="Remove track">
                  ✕
                </Button>
              </div>
            </div>
            {track.description && (
              <p className="mt-1 text-xs text-muted-foreground">{track.description}</p>
            )}
            {open === name && (
              <div className="mt-3">
                <LifecycleEditor
                  value={track as LifecycleDefinition & { enabled?: boolean }}
                  onChange={(machine) => update(name, machine)}
                />
              </div>
            )}
          </div>
        );
      })}
      <div className="flex flex-wrap items-end gap-2">
        {missing.map((template) => (
          <Button
            key={template.name}
            size="sm"
            variant="outline"
            onClick={() => addTemplate(template)}
          >
            Add {template.label.toLowerCase()} track
          </Button>
        ))}
        <div>
          <Label htmlFor="new-track">Or a new track named</Label>
          <Input
            id="new-track"
            value={newName}
            placeholder="onboarding_health"
            onChange={(event) => setNewName(event.target.value)}
            onKeyDown={(event) => event.key === "Enter" && addEmpty()}
            className="h-8 w-48 text-[11px]"
          />
        </div>
        <Button size="sm" variant="outline" onClick={addEmpty}>
          Add
        </Button>
      </div>
    </div>
  );
}
