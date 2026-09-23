"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { MemoryTable } from "@/components/memory-table";
import { Card } from "@/components/ui/card";
import { Select } from "@/components/ui/input";
import { ErrorState, LoadingRow, PageHeader } from "@/components/ui/states";
import { useSession } from "@/hooks/use-session";
import { api } from "@/lib/api";
import type { Memory, Page } from "@/lib/types";

const TYPES = [
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

export default function MemoriesPage() {
  const { projectId } = useSession();
  const [type, setType] = useState("");
  const [status, setStatus] = useState("active");

  const memories = useQuery({
    queryKey: ["memories", projectId, type, status],
    queryFn: () =>
      api<Page<Memory>>(`/v1/projects/${projectId}/memories`, {
        query: { type: type || undefined, status: status || undefined, limit: 100 },
      }),
    enabled: Boolean(projectId),
  });

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Memory"
        title="Memory explorer"
        description="Everything this project believes, with the evidence and provenance behind it."
        actions={
          <>
            <Select value={type} onChange={(event) => setType(event.target.value)} className="w-44">
              <option value="">All types</option>
              {TYPES.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </Select>
            <Select
              value={status}
              onChange={(event) => setStatus(event.target.value)}
              className="w-44"
            >
              <option value="active">Active</option>
              <option value="superseded">Superseded</option>
              <option value="expired">Expired</option>
              <option value="">Any status</option>
            </Select>
          </>
        }
      />

      <Card>
        {memories.isLoading && <LoadingRow />}
        {memories.error && <ErrorState error={memories.error} />}
        {memories.data && <MemoryTable memories={memories.data.data} withheld={memories.data.withheld ?? 0} />}
      </Card>
    </div>
  );
}
