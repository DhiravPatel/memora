"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { FreshnessBadge } from "@/components/freshness";
import { MemoryTypeBadge, RestrictedBadge, ScoreBar, StatusBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { EmptyState } from "@/components/ui/states";
import { Table, TD, TH, THead, TR } from "@/components/ui/table";
import { useSession } from "@/hooks/use-session";
import { api } from "@/lib/api";
import { formatRelative } from "@/lib/format";
import type { Memory } from "@/lib/types";

export function MemoryTable({
  memories,
  allowFeedback = true,
  withheld = 0,
}: {
  memories: Memory[];
  allowFeedback?: boolean;
  /** How many the reader's clearance hid. Shown rather than silently dropped. */
  withheld?: number;
}) {
  const [expanded, setExpanded] = useState<string | null>(null);

  if (!memories.length) {
    return (
      <>
        <EmptyState
          title={withheld ? "Nothing you can see here" : "No memories yet"}
          description={
            withheld
              ? "Every memory for this view is restricted by the project's policy."
              : "Memories appear once events with real signal have been processed."
          }
        />
        <WithheldNote count={withheld} />
      </>
    );
  }

  return (
    <>
      <Table className="min-w-[1020px]">
        <THead>
          <TR>
            <TH className="min-w-[340px]">Memory</TH>
            <TH className="w-28">Type</TH>
            <TH className="w-28">Importance</TH>
            <TH className="w-28">Confidence</TH>
            <TH className="w-14">Evid.</TH>
            <TH className="w-24 whitespace-nowrap">Last seen</TH>
            <TH className="w-24">Status</TH>
            {allowFeedback && <TH className="w-28 text-right">Verdict</TH>}
          </TR>
        </THead>
        <tbody>
          {memories.map((memory) => (
            <TR key={memory.id}>
              <TD className="min-w-[340px]">
                <button
                  className="text-left"
                  onClick={() => setExpanded(expanded === memory.id ? null : memory.id)}
                >
                  <p className="text-sm leading-snug">{memory.content}</p>
                  {memory.sensitivity === "restricted" && (
                    <span className="mt-1.5 inline-block">
                      <RestrictedBadge
                        reason={memory.metadata?.restricted_by as string | undefined}
                      />
                    </span>
                  )}
                  {memory.freshness && memory.freshness.state !== "active" && (
                    <span className="ml-1 mt-1.5 inline-block">
                      <FreshnessBadge freshness={memory.freshness} />
                    </span>
                  )}
                  {expanded === memory.id && (
                    <div className="mt-2 border-l-2 border-accent bg-surface-2 px-3 py-2">
                      <p className="label">Provenance</p>
                      <p className="mt-1 font-mono text-[10px] leading-relaxed text-muted-foreground">
                        {memory.id} · source {memory.source} · events{" "}
                        {memory.source_event_ids.join(", ") || "—"}
                      </p>
                      {memory.freshness && (
                        <p className="mt-1 text-[11px] text-muted-foreground">
                          {memory.freshness.reasons.join(" ") ||
                            `Last evidence ${formatRelative(memory.freshness.evidence_at)}.`}{" "}
                          Effective confidence{" "}
                          {Math.round(memory.freshness.effective_confidence * 100)}%.
                        </p>
                      )}
                    </div>
                  )}
                </button>
              </TD>
              <TD>
                <MemoryTypeBadge type={memory.type} />
              </TD>
              <TD>
                <ScoreBar value={memory.importance} />
              </TD>
              <TD>
                <ScoreBar value={memory.confidence} />
              </TD>
              <TD className="numeric text-[11px] text-muted-foreground">{memory.evidence_count}</TD>
              <TD className="label whitespace-nowrap">{formatRelative(memory.last_seen_at)}</TD>
              <TD>
                <StatusBadge status={memory.status} />
              </TD>
              {allowFeedback && (
                <TD className="text-right">
                  <MemoryFeedback memory={memory} />
                </TD>
              )}
            </TR>
          ))}
        </tbody>
      </Table>
      <WithheldNote count={withheld} />
    </>
  );
}

/** Says how many memories were hidden, so an incomplete list never looks complete. */
function WithheldNote({ count }: { count: number }) {
  if (!count) return null;
  return (
    <p className="mt-3 border-l-2 border-danger bg-surface-2 px-3 py-2 text-[11px] text-muted-foreground">
      <strong className="text-danger">{count}</strong> {count === 1 ? "memory is" : "memories are"}{" "}
      restricted by this project's policy and not shown. A key with the{" "}
      <code className="font-mono">memory:restricted</code> scope, or an admin, can read them.
    </p>
  );
}

/** Confirm, reject or correct a memory. Confidence moves; history is preserved. */
function MemoryFeedback({ memory }: { memory: Memory }) {
  const { projectId } = useSession();
  const queryClient = useQueryClient();
  const [correcting, setCorrecting] = useState(false);
  const [draft, setDraft] = useState(memory.content);

  const submit = useMutation({
    mutationFn: (body: { verdict: string; content?: string }) =>
      api(`/v1/projects/${projectId}/memories/${memory.id}/feedback`, {
        method: "POST",
        body,
      }),
    onSuccess: () => {
      setCorrecting(false);
      queryClient.invalidateQueries({ queryKey: ["customer-memories"] });
      queryClient.invalidateQueries({ queryKey: ["memories"] });
      queryClient.invalidateQueries({ queryKey: ["customer-health"] });
    },
  });

  if (memory.status !== "active") {
    return <span className="label">locked</span>;
  }

  if (correcting) {
    return (
      <div className="w-72 space-y-1.5 text-left">
        <Input value={draft} onChange={(event) => setDraft(event.target.value)} className="h-8" />
        <div className="flex gap-1.5">
          <Button
            size="sm"
            loading={submit.isPending}
            onClick={() => submit.mutate({ verdict: "correct", content: draft })}
          >
            Save
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setCorrecting(false)}>
            Cancel
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex justify-end gap-1">
      <Button
        size="sm"
        variant="outline"
        title="Confirm this memory is correct"
        loading={submit.isPending && submit.variables?.verdict === "confirm"}
        onClick={() => submit.mutate({ verdict: "confirm" })}
      >
        Yes
      </Button>
      <Button
        size="sm"
        variant="ghost"
        title="Reject this memory"
        loading={submit.isPending && submit.variables?.verdict === "reject"}
        onClick={() => submit.mutate({ verdict: "reject" })}
      >
        No
      </Button>
      <Button
        size="sm"
        variant="ghost"
        title="Correct the wording"
        onClick={() => setCorrecting(true)}
      >
        Edit
      </Button>
    </div>
  );
}
