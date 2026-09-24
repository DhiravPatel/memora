"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { StateBadge } from "@/components/lifecycle";
import { Card } from "@/components/ui/card";
import { Input, Select } from "@/components/ui/input";
import { EmptyState, ErrorState, LoadingRow, PageHeader } from "@/components/ui/states";
import { Table, TD, TH, THead, TR } from "@/components/ui/table";
import { useSession } from "@/hooks/use-session";
import { api } from "@/lib/api";
import { formatRelative } from "@/lib/format";
import type { Customer, LifecycleOverview, Page } from "@/lib/types";

export default function CustomersPage() {
  return (
    <Suspense fallback={<LoadingRow />}>
      <Customers />
    </Suspense>
  );
}

function Customers() {
  const { projectId } = useSession();
  const [search, setSearch] = useState("");
  // `?state=at_risk&track=engagement` arrives from the Overview's lifecycle card. The filter
  // holds "track:state" so one select covers every track.
  const params = useSearchParams();
  const [filter, setFilter] = useState(
    params.get("state") ? `${params.get("track") ?? "lifecycle"}:${params.get("state")}` : "",
  );
  const [track, state] = filter ? (filter.split(":") as [string, string]) : ["lifecycle", ""];

  const lifecycle = useQuery({
    queryKey: ["lifecycle", projectId],
    queryFn: () => api<LifecycleOverview>(`/v1/projects/${projectId}/lifecycle`),
    enabled: Boolean(projectId),
  });

  const customers = useQuery({
    queryKey: ["customers", projectId, search, filter],
    queryFn: () =>
      state
        ? api<Page<Customer>>(`/v1/projects/${projectId}/lifecycle/customers`, {
            query: { state, track, limit: 100 },
          })
        : api<Page<Customer>>(`/v1/projects/${projectId}/customers`, {
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
          <>
            {(lifecycle.data?.enabled || lifecycle.data?.tracks?.length) && (
              <Select
                value={filter}
                onChange={(event) => setFilter(event.target.value)}
                className="w-56"
              >
                <option value="">Any lifecycle state</option>
                {lifecycle.data?.enabled && (
                  <optgroup label="Lifecycle">
                    {(lifecycle.data.states ?? []).map((item) => (
                      <option key={item} value={`lifecycle:${item}`}>
                        {item.replace(/_/g, " ")} · {lifecycle.data?.counts[item] ?? 0}
                      </option>
                    ))}
                  </optgroup>
                )}
                {(lifecycle.data?.tracks ?? [])
                  .filter((item) => item.enabled)
                  .map((item) => (
                    <optgroup key={item.track} label={item.label}>
                      {item.states.map((name) => (
                        <option key={name} value={`${item.track}:${name}`}>
                          {name.replace(/_/g, " ")} · {item.counts[name] ?? 0}
                        </option>
                      ))}
                    </optgroup>
                  ))}
              </Select>
            )}
            <Input
              placeholder="SEARCH…"
              className="w-64 uppercase tracking-label"
              value={search}
              disabled={Boolean(filter)}
              onChange={(event) => setSearch(event.target.value)}
            />
          </>
        }
      />

      {state && (
        <p className="label">
          Showing customers currently <StateBadge state={state} />
          {track !== "lifecycle" && ` on the ${track.replace(/_/g, " ")} track`} ·{" "}
          <button className="hover:text-accent" onClick={() => setFilter("")}>
            clear
          </button>
        </p>
      )}

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
