"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { AskMemory } from "@/components/ask-memory";
import { Card, CardContent } from "@/components/ui/card";
import { Label, Select } from "@/components/ui/input";
import { EmptyState, LoadingRow, PageHeader } from "@/components/ui/states";
import { useSession } from "@/hooks/use-session";
import { api } from "@/lib/api";
import type { Customer, Page } from "@/lib/types";

export default function PlaygroundPage() {
  const { projectId } = useSession();
  const [customerId, setCustomerId] = useState("");

  const customers = useQuery({
    queryKey: ["customers", projectId, "playground"],
    queryFn: () =>
      api<Page<Customer>>(`/v1/projects/${projectId}/customers`, { query: { limit: 100 } }),
    enabled: Boolean(projectId),
  });

  useEffect(() => {
    const first = customers.data?.data[0];
    if (!customerId && first) setCustomerId(first.id);
  }, [customers.data, customerId]);

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Pipeline"
        title="Memory playground"
        description="Ask a question and inspect exactly which memories answered it, how they were retrieved, and why."
      />

      <Card>
        <CardContent className="py-4">
          <Label htmlFor="customer">Customer</Label>
          {customers.isLoading ? (
            <LoadingRow />
          ) : (
            <Select
              id="customer"
              value={customerId}
              onChange={(event) => setCustomerId(event.target.value)}
              className="max-w-sm"
            >
              {customers.data?.data.map((customer) => (
                <option key={customer.id} value={customer.id}>
                  {customer.name || customer.external_id}
                </option>
              ))}
            </Select>
          )}
        </CardContent>
      </Card>

      {customerId ? (
        <AskMemory customerId={customerId} />
      ) : (
        <Card>
          <EmptyState
            title="No customers yet"
            description="Send an event first — customers are created automatically."
          />
        </Card>
      )}
    </div>
  );
}
