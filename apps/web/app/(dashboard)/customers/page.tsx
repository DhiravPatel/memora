"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { EmptyState, ErrorState, LoadingRow, PageHeader } from "@/components/ui/states";
import { Table, TD, TH, THead, TR } from "@/components/ui/table";
import { useSession } from "@/hooks/use-session";
import { api } from "@/lib/api";
import { formatRelative } from "@/lib/format";
import type { Customer, Page } from "@/lib/types";

export default function CustomersPage() {
  const { projectId } = useSession();
  const [search, setSearch] = useState("");

  const customers = useQuery({
    queryKey: ["customers", projectId, search],
    queryFn: () =>
      api<Page<Customer>>(`/v1/projects/${projectId}/customers`, {
        query: { search, limit: 100 },
      }),
    enabled: Boolean(projectId),
  });

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Memory"
        title="Customer explorer"
        description="Search by id, email or name, then open a profile to see everything the memory holds."
        actions={
          <Input
            placeholder="SEARCH…"
            className="w-64 uppercase tracking-label"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        }
      />

      <Card>
        {customers.isLoading && <LoadingRow />}
        {customers.error && <ErrorState error={customers.error} />}
        {customers.data?.data.length === 0 && (
          <EmptyState
            title="No customers yet"
            description="Customers are created automatically the first time you send an event for them."
          />
        )}
        {!!customers.data?.data.length && (
          <Table>
            <THead>
              <TR>
                <TH>Customer</TH>
                <TH className="w-64">Email</TH>
                <TH className="w-32">Last activity</TH>
                <TH className="w-32">Created</TH>
              </TR>
            </THead>
            <tbody>
              {customers.data.data.map((customer) => (
                <TR key={customer.id}>
                  <TD>
                    <Link
                      href={`/customers/${customer.id}`}
                      className="text-sm font-semibold hover:text-accent"
                    >
                      {customer.name || customer.external_id}
                    </Link>
                    <p className="label mt-0.5">{customer.external_id}</p>
                  </TD>
                  <TD className="font-mono text-[11px] text-muted-foreground">
                    {customer.email ?? "—"}
                  </TD>
                  <TD className="label">{formatRelative(customer.last_event_at)}</TD>
                  <TD className="label">{formatRelative(customer.created_at)}</TD>
                </TR>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </div>
  );
}
