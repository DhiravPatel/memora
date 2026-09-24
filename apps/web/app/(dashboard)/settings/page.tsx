"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import {
  Field,
  MapEditor,
  NumberControl,
  PolicyEditor,
  SliderControl,
  ToggleControl,
  WeightsEditor,
} from "@/components/settings/controls";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label, Textarea } from "@/components/ui/input";
import { ErrorState, LoadingRow, PageHeader } from "@/components/ui/states";
import { useSession } from "@/hooks/use-session";
import { api } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { cn } from "@/lib/utils";
import { GuardrailsEditor } from "@/components/settings/guardrails-editor";
import { LifecycleEditor } from "@/components/settings/lifecycle-editor";
import type {
  GuardrailSettings,
  LearnedTerm,
  LifecycleDefinition,
  ProjectSettings,
  RestrictionRule,
  SettingField,
  Vocabulary,
} from "@/lib/types";

type Values = Record<string, any>;

const TABS = ["Engine", "Project", "Account"] as const;

export default function SettingsPage() {
  const { project, projectId, projects, refreshProjects, selectProject, user, logout } =
    useSession();
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<(typeof TABS)[number]>("Engine");

  const settings = useQuery({
    queryKey: ["project-settings", projectId],
    queryFn: () => api<ProjectSettings>(`/v1/projects/${projectId}/settings`),
    enabled: Boolean(projectId),
  });

  const [draft, setDraft] = useState<Values | null>(null);
  useEffect(() => {
    if (settings.data) setDraft(structuredClone(settings.data.values));
  }, [settings.data]);

  const save = useMutation({
    mutationFn: (values: Values) =>
      api<ProjectSettings>(`/v1/projects/${projectId}/settings`, {
        method: "PUT",
        body: { settings: values },
      }),
    onSuccess: (updated) => {
      queryClient.setQueryData(["project-settings", projectId], updated);
      setDraft(structuredClone(updated.values));
      refreshProjects();
    },
  });

  const dirtyKeys = useMemo(() => {
    if (!settings.data || !draft) return [] as string[];
    return Object.keys(draft).filter(
      (key) => JSON.stringify(draft[key]) !== JSON.stringify(settings.data!.values[key]),
    );
  }, [draft, settings.data]);

  if (!projectId) {
    return (
      <div className="space-y-6">
        <PageHeader eyebrow="System" title="Settings" />
        <Card>
          <CardContent className="py-8 text-center text-xs text-muted-foreground">
            Create a project first.
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="max-w-5xl space-y-6 pb-24">
      <PageHeader
        eyebrow={`Project · ${project?.name ?? "—"}`}
        title="Settings"
        description="Every value here is validated on write and enforced by the engine — there is no unchecked JSON path into the pipeline."
        actions={
          <div className="flex gap-px border border-border bg-border">
            {TABS.map((item) => (
              <button
                key={item}
                onClick={() => setTab(item)}
                className={cn(
                  "px-4 py-2 font-mono text-[11px] uppercase tracking-label transition-colors",
                  tab === item
                    ? "bg-accent text-accent-foreground"
                    : "bg-surface text-muted-foreground hover:bg-surface-2",
                )}
              >
                {item}
              </button>
            ))}
          </div>
        }
      />

      {tab === "Engine" && (
        <>
          {settings.isLoading && <LoadingRow />}
          {settings.error && <ErrorState error={settings.error} />}
          {settings.data && draft && (
            <EngineSettings
              schema={settings.data}
              draft={draft}
              dirtyKeys={dirtyKeys}
              onChange={(key, value) => setDraft({ ...draft, [key]: value })}
            />
          )}
          {save.error && <ErrorState error={save.error} />}
        </>
      )}

      {tab === "Project" && <ProjectPanel />}
      {tab === "Account" && <AccountPanel email={user?.email ?? ""} onSignOut={logout} />}

      {tab === "Engine" && dirtyKeys.length > 0 && settings.data && draft && (
        <div className="fixed bottom-0 left-0 right-0 z-20 border-t border-border-strong bg-surface/95 px-5 py-3 backdrop-blur md:pl-64">
          <div className="mx-auto flex max-w-5xl items-center justify-between gap-4">
            <p className="label">
              {dirtyKeys.length} unsaved change
              {dirtyKeys.length === 1 ? "" : "s"} · {dirtyKeys.join(", ")}
            </p>
            <div className="flex gap-2">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setDraft(structuredClone(settings.data!.values))}
              >
                Discard
              </Button>
              <Button size="sm" loading={save.isPending} onClick={() => save.mutate(draft)}>
                Save changes
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function EngineSettings({
  schema,
  draft,
  dirtyKeys,
  onChange,
}: {
  schema: ProjectSettings;
  draft: Values;
  dirtyKeys: string[];
  onChange: (key: string, value: unknown) => void;
}) {
  const [showAdvanced, setShowAdvanced] = useState(false);

  return (
    <div className="space-y-5">
      {schema.groups.map((group) => {
        const fields = schema.fields.filter((field) => field.group === group.key);
        if (!fields.length) return null;
        return (
          <Card key={group.key}>
            <CardHeader>
              <CardTitle>{group.label}</CardTitle>
            </CardHeader>
            <div className="divide-y divide-border">
              {fields.map((field) => (
                <Control
                  key={field.key}
                  field={field}
                  value={draft[field.key]}
                  defaultValue={schema.defaults[field.key]}
                  changed={dirtyKeys.includes(field.key)}
                  onChange={(value) => onChange(field.key, value)}
                />
              ))}
            </div>
          </Card>
        );
      })}

      <VocabularyCard />

      <Card>
        <CardHeader className="flex items-center justify-between">
          <div>
            <CardTitle>Advanced</CardTitle>
            <CardDescription>
              The stored JSON, exactly as the engine reads it. Unknown keys are preserved for
              forward compatibility.
            </CardDescription>
          </div>
          <Button size="sm" variant="ghost" onClick={() => setShowAdvanced((value) => !value)}>
            {showAdvanced ? "Hide" : "Show"}
          </Button>
        </CardHeader>
        {showAdvanced && (
          <CardContent>
            <pre className="max-h-96 overflow-auto border border-border bg-surface-2 p-3 font-mono text-[10px] leading-relaxed text-muted-foreground">
              {JSON.stringify(draft, null, 2)}
            </pre>
          </CardContent>
        )}
      </Card>
    </div>
  );
}

/**
 * The project's vocabulary: what the corpus taught it, and what a person told it.
 *
 * Both live in one list because they do one job — widening retrieval — but they are
 * marked differently, because "we measured this" and "somebody asserted this" are
 * different kinds of claim and you should be able to tell them apart at a glance.
 */
function VocabularyCard() {
  const { projectId } = useSession();
  const queryClient = useQueryClient();
  const [showRejected, setShowRejected] = useState(false);
  const [term, setTerm] = useState("");
  const [synonym, setSynonym] = useState("");

  const status = showRejected ? "rejected" : "active";
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["vocabulary", projectId] });

  const vocabulary = useQuery({
    queryKey: ["vocabulary", projectId, status],
    queryFn: () =>
      api<Vocabulary>(`/v1/projects/${projectId}/vocabulary`, {
        query: { limit: 80, status },
      }),
    enabled: Boolean(projectId),
  });

  const rebuild = useMutation({
    mutationFn: () =>
      api<{ message: string }>(`/v1/projects/${projectId}/vocabulary/rebuild`, {
        method: "POST",
      }),
    // The job runs in the worker, so give it a moment before re-reading.
    onSuccess: () => setTimeout(invalidate, 2500),
  });

  const teach = useMutation({
    mutationFn: () =>
      api<LearnedTerm>(`/v1/projects/${projectId}/vocabulary`, {
        method: "POST",
        body: { term, synonym },
      }),
    onSuccess: () => {
      setTerm("");
      setSynonym("");
      invalidate();
    },
  });

  const decide = useMutation({
    mutationFn: (input: { pair: LearnedTerm; restore: boolean }) =>
      api<{ message: string }>(
        `/v1/projects/${projectId}/vocabulary/${encodeURIComponent(input.pair.term)}/${encodeURIComponent(input.pair.synonym)}`,
        { method: "DELETE", query: { restore: input.restore } },
      ),
    onSuccess: invalidate,
  });

  return (
    <Card>
      <CardHeader className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <CardTitle>Vocabulary</CardTitle>
          <CardDescription>
            Terms this project treats as the same thing when retrieving — so a question about
            &ldquo;the loader&rdquo; finds memories that only ever say &ldquo;importer&rdquo;. Mined
            from your own memories nightly, and extended by anything you teach it here.
            {vocabulary.data?.last_mined_at
              ? ` Last mined ${formatDate(vocabulary.data.last_mined_at)}.`
              : ""}
          </CardDescription>
        </div>
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setShowRejected((value) => !value)}
            title="Pairs somebody threw out, which mining will not relearn"
          >
            {showRejected ? "Active" : `Rejected (${vocabulary.data?.rejected ?? 0})`}
          </Button>
          <Button
            size="sm"
            variant="outline"
            loading={rebuild.isPending}
            onClick={() => rebuild.mutate()}
          >
            Rebuild
          </Button>
        </div>
      </CardHeader>

      <CardContent className="space-y-4">
        {vocabulary.isLoading && <LoadingRow />}
        {teach.error && <ErrorState error={teach.error} />}
        {rebuild.isSuccess && <p className="label text-accent">{rebuild.data?.message}</p>}

        {vocabulary.data && vocabulary.data.terms.length === 0 && (
          <p className="text-xs leading-relaxed text-muted-foreground">
            {showRejected
              ? "Nothing has been rejected."
              : "Nothing learned yet. A project needs a few dozen memories before a pair of terms can be said to travel together rather than to have met once — you can teach it one below in the meantime."}
          </p>
        )}

        {!!vocabulary.data?.terms.length && (
          <div className="flex flex-wrap gap-1.5">
            {vocabulary.data.terms.map((pair) => (
              <span
                key={`${pair.term}-${pair.synonym}`}
                title={
                  pair.source === "curated"
                    ? `Taught by a person${pair.note ? `: ${pair.note}` : ""}`
                    : `${Math.round(pair.score * 100)}% together, across ${pair.support} memories`
                }
                className={cn(
                  "group inline-flex items-center gap-1.5 rounded-sm border px-2 py-1 font-mono text-[10px]",
                  pair.source === "curated"
                    ? "border-accent/40 bg-accent-soft text-accent"
                    : "border-border bg-surface-2 text-muted-foreground",
                )}
              >
                {pair.term} <span className="opacity-60">~</span> {pair.synonym}
                {pair.source === "mined" && (
                  <span className="text-[9px] opacity-70">{pair.support}</span>
                )}
                <button
                  onClick={() => decide.mutate({ pair, restore: showRejected })}
                  disabled={decide.isPending}
                  title={showRejected ? "Allow this pair again" : "Throw this pair out for good"}
                  className="ml-0.5 opacity-0 transition-opacity hover:text-danger focus-visible:opacity-100 group-hover:opacity-100"
                >
                  {showRejected ? "↩" : "✕"}
                </button>
              </span>
            ))}
          </div>
        )}

        {!showRejected && (
          <form
            className="flex flex-wrap items-center gap-2 border-t border-border pt-4"
            onSubmit={(event) => {
              event.preventDefault();
              if (term.trim() && synonym.trim()) teach.mutate();
            }}
          >
            <Input
              value={term}
              onChange={(event) => setTerm(event.target.value)}
              placeholder="TERM"
              className="h-8 w-40"
            />
            <span className="label">means</span>
            <Input
              value={synonym}
              onChange={(event) => setSynonym(event.target.value)}
              placeholder="SYNONYM"
              className="h-8 w-40"
            />
            <Button size="sm" type="submit" loading={teach.isPending}>
              Teach
            </Button>
          </form>
        )}
      </CardContent>
    </Card>
  );
}

function Control({
  field,
  value,
  defaultValue,
  changed,
  onChange,
}: {
  field: SettingField;
  value: any;
  defaultValue: any;
  changed: boolean;
  onChange: (value: unknown) => void;
}) {
  const isDefault = JSON.stringify(value) === JSON.stringify(defaultValue);
  const reset = !isDefault ? (
    <button
      onClick={() => onChange(structuredClone(defaultValue))}
      className="label hover:text-accent"
      title="Reset to the deployment default"
    >
      reset
    </button>
  ) : null;

  if (field.kind === "boolean") {
    return (
      <Field label={field.label} help={field.help} changed={changed}>
        {reset}
        <ToggleControl value={Boolean(value)} onChange={onChange} labels={["enabled", "off"]} />
      </Field>
    );
  }

  if (field.kind === "percent") {
    return (
      <Field label={field.label} help={field.help} changed={changed}>
        {reset}
        <SliderControl
          value={Number(value ?? 0)}
          min={field.minimum ?? 0}
          max={field.maximum ?? 1}
          step={field.step ?? 0.01}
          onChange={onChange}
        />
      </Field>
    );
  }

  if (field.kind === "number") {
    return (
      <Field label={field.label} help={field.help} changed={changed}>
        {reset}
        <NumberControl
          value={Number(value ?? 0)}
          min={field.minimum ?? undefined}
          max={field.maximum ?? undefined}
          step={field.step ?? 1}
          unit={field.unit}
          onChange={onChange}
        />
      </Field>
    );
  }

  if (field.kind === "lifecycle") {
    return (
      <Field
        label={field.label}
        help={field.help}
        changed={changed}
        hint={
          <LifecycleEditor
            value={(value ?? { states: [], initial: "", transitions: [] }) as LifecycleDefinition}
            onChange={onChange}
          />
        }
      >
        {reset}
      </Field>
    );
  }

  if (field.kind === "guardrails") {
    return (
      <Field
        label={field.label}
        help={field.help}
        changed={changed}
        hint={
          <GuardrailsEditor
            value={
              (value ?? { disabled: [], rules: [], approval_ttl_hours: 24 }) as GuardrailSettings
            }
            builtins={field.keys}
            onChange={onChange}
          />
        }
      >
        {reset}
      </Field>
    );
  }

  if (field.kind === "policies") {
    return (
      <Field
        label={field.label}
        help={field.help}
        changed={changed}
        hint={
          <PolicyEditor
            rules={(Array.isArray(value) ? value : []) as RestrictionRule[]}
            memoryTypes={field.keys}
            onChange={onChange}
          />
        }
      >
        {reset}
      </Field>
    );
  }

  if (field.kind === "weights") {
    return (
      <Field
        label={field.label}
        help={field.help}
        changed={changed}
        hint={
          <WeightsEditor
            weights={(value ?? {}) as Record<string, number>}
            keys={field.keys}
            onChange={onChange}
          />
        }
      >
        {reset}
      </Field>
    );
  }

  // map
  return (
    <Field
      label={field.label}
      help={field.help}
      changed={changed}
      hint={
        <MapEditor
          entries={(value ?? {}) as Record<string, number>}
          fixedKeys={field.keys.length ? field.keys : undefined}
          min={field.minimum ?? 0}
          max={field.maximum ?? 1}
          step={field.step ?? 0.01}
          unit={field.unit}
          onChange={onChange}
        />
      }
    >
      {reset}
    </Field>
  );
}

function ProjectPanel() {
  const { project, projectId, projects, refreshProjects, selectProject } = useSession();
  const [name, setName] = useState("");
  const [newProjectName, setNewProjectName] = useState("");
  const [revealedKey, setRevealedKey] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState("");

  useEffect(() => setName(project?.name ?? ""), [project?.name]);

  const rename = useMutation({
    mutationFn: () => api(`/v1/projects/${projectId}`, { method: "PATCH", body: { name } }),
    onSuccess: refreshProjects,
  });

  const create = useMutation({
    mutationFn: () =>
      api<{ id: string; api_key: string }>("/v1/projects", {
        method: "POST",
        body: { name: newProjectName },
      }),
    onSuccess: (created) => {
      setRevealedKey(created.api_key);
      setNewProjectName("");
      refreshProjects();
      selectProject(created.id);
    },
  });

  const remove = useMutation({
    mutationFn: () => api(`/v1/projects/${projectId}`, { method: "DELETE" }),
    onSuccess: () => {
      setConfirmDelete("");
      refreshProjects();
      const next = projects.find((item) => item.id !== projectId);
      if (next) selectProject(next.id);
    },
  });

  const error = rename.error ?? create.error ?? remove.error ?? null;

  return (
    <div className="space-y-5">
      {revealedKey && (
        <Card className="border-accent/60 bg-accent-soft shadow-[0_0_0_4px_hsl(var(--accent)/0.08)]">
          <CardHeader>
            <CardTitle className="text-accent">Project created</CardTitle>
            <CardDescription>Its first API key is shown once.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <code className="block break-all border border-border bg-surface-2 p-3 font-mono text-xs text-accent">
              {revealedKey}
            </code>
            <div className="flex gap-2">
              <Button size="sm" onClick={() => navigator.clipboard.writeText(revealedKey)}>
                Copy
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setRevealedKey(null)}>
                Done
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {error && <ErrorState error={error} />}

      <Card>
        <CardHeader>
          <CardTitle>This project</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <Label htmlFor="project-name">Name</Label>
              <div className="flex gap-2">
                <Input
                  id="project-name"
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                />
                <Button
                  size="sm"
                  loading={rename.isPending}
                  disabled={!name.trim() || name === project?.name}
                  onClick={() => rename.mutate()}
                >
                  Rename
                </Button>
              </div>
            </div>
            <div>
              <Label>Identifiers</Label>
              <div className="space-y-1 border border-border bg-surface-2 px-3 py-2">
                <p className="font-mono text-[11px]">{project?.id}</p>
                <p className="label">
                  key prefix {project?.api_key_prefix}… · created{" "}
                  {project ? formatDate(project.created_at) : "—"}
                </p>
              </div>
            </div>
          </div>
          <p className="label">
            API keys moved to their own screen —{" "}
            <Link href="/keys" className="text-accent hover:underline">
              manage scoped keys →
            </Link>
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Projects</CardTitle>
          <CardDescription>Each project is an isolated tenant with its own keys.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="space-y-px">
            {projects.map((item) => (
              <button
                key={item.id}
                onClick={() => selectProject(item.id)}
                className={cn(
                  "flex w-full items-center justify-between border-l-2 px-3 py-2 text-left transition-colors",
                  item.id === projectId
                    ? "border-accent bg-surface-2 text-accent"
                    : "border-transparent text-muted-foreground hover:bg-surface-2 hover:text-foreground",
                )}
              >
                <span className="font-mono text-[11px] uppercase tracking-label">{item.name}</span>
                <span className="label">{item.id}</span>
              </button>
            ))}
          </div>
          <div className="flex gap-2 border-t border-border pt-3">
            <Input
              placeholder="NEW PROJECT"
              className="uppercase tracking-label"
              value={newProjectName}
              onChange={(event) => setNewProjectName(event.target.value)}
            />
            <Button
              loading={create.isPending}
              disabled={!newProjectName.trim()}
              onClick={() => create.mutate()}
            >
              Create
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card className="border-danger/60">
        <CardHeader>
          <CardTitle className="text-danger">Danger zone</CardTitle>
          <CardDescription>
            Deleting a project removes its customers, events, memories, keys and webhooks. This
            cannot be undone.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap items-end gap-3">
          <div className="min-w-[260px] flex-1">
            <Label htmlFor="confirm-delete">Type the project name to confirm</Label>
            <Input
              id="confirm-delete"
              placeholder={project?.name}
              value={confirmDelete}
              onChange={(event) => setConfirmDelete(event.target.value)}
            />
          </div>
          <Button
            variant="danger"
            loading={remove.isPending}
            disabled={confirmDelete !== project?.name || projects.length < 2}
            onClick={() => remove.mutate()}
          >
            Delete project
          </Button>
          {projects.length < 2 && <p className="label">Your last project cannot be deleted.</p>}
        </CardContent>
      </Card>
    </div>
  );
}

function AccountPanel({ email, onSignOut }: { email: string; onSignOut: () => void }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [done, setDone] = useState<string | null>(null);

  const change = useMutation({
    mutationFn: () =>
      api<{ access_token: string; refresh_token: string }>("/v1/auth/change-password", {
        method: "POST",
        body: { current_password: current, new_password: next },
      }),
    onSuccess: (tokens) => {
      // The response carries fresh tokens; every other session is now signed out.
      localStorage.setItem("aiml.access_token", tokens.access_token);
      localStorage.setItem("aiml.refresh_token", tokens.refresh_token);
      setCurrent("");
      setNext("");
      setConfirm("");
      setDone("Password changed. Other sessions were signed out.");
    },
  });

  const signOutEverywhere = useMutation({
    mutationFn: () =>
      api<{ access_token: string; refresh_token: string }>("/v1/auth/sign-out-everywhere", {
        method: "POST",
      }),
    onSuccess: () => onSignOut(),
  });

  const mismatch = next.length > 0 && confirm.length > 0 && next !== confirm;

  return (
    <div className="space-y-5">
      <Card>
        <CardHeader>
          <CardTitle>Account</CardTitle>
          <CardDescription>{email}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-3">
            <div>
              <Label htmlFor="current-password">Current password</Label>
              <Input
                id="current-password"
                type="password"
                autoComplete="current-password"
                value={current}
                onChange={(event) => setCurrent(event.target.value)}
              />
            </div>
            <div>
              <Label htmlFor="new-password">New password</Label>
              <Input
                id="new-password"
                type="password"
                autoComplete="new-password"
                minLength={8}
                value={next}
                onChange={(event) => setNext(event.target.value)}
              />
            </div>
            <div>
              <Label htmlFor="confirm-password">Confirm</Label>
              <Input
                id="confirm-password"
                type="password"
                autoComplete="new-password"
                value={confirm}
                onChange={(event) => setConfirm(event.target.value)}
              />
            </div>
          </div>
          {mismatch && <p className="font-mono text-[11px] text-danger">Passwords do not match.</p>}
          {change.error && (
            <p className="font-mono text-[11px] text-danger">
              {change.error instanceof Error ? change.error.message : "Could not change password."}
            </p>
          )}
          {done && <p className="font-mono text-[11px] text-accent">{done}</p>}
          <Button
            loading={change.isPending}
            disabled={!current || next.length < 8 || mismatch}
            onClick={() => change.mutate()}
          >
            Change password
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Sessions</CardTitle>
          <CardDescription>
            Signing out everywhere invalidates every token this account holds, on every device,
            immediately.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center gap-3">
          <Button
            variant="secondary"
            loading={signOutEverywhere.isPending}
            onClick={() => signOutEverywhere.mutate()}
          >
            Sign out everywhere
          </Button>
          <Badge>you will be returned to the login screen</Badge>
        </CardContent>
      </Card>
    </div>
  );
}
