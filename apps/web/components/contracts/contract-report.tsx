"use client";

import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { StatTile } from "@/components/ui/card";
import { Table, TD, TH, THead, TR } from "@/components/ui/table";
import { formatDate, formatNumber, formatRelative } from "@/lib/format";
import type { ContractViolation, EventContract } from "@/lib/types";

const RULES: Record<string, string> = {
  required: "missing",
  type: "wrong type",
  enum: "not allowed",
  minimum: "too small",
  maximum: "too large",
  max_length: "too long",
  pattern: "wrong format",
  unknown_field: "not in contract",
};

function Violations({ items }: { items: ContractViolation[] }) {
  return (
    <ul className="space-y-0.5">
      {items.map((item, index) => (
        <li
          key={`${item.path}-${index}`}
          className="text-[11px] leading-snug text-muted-foreground"
        >
          {item.message}
        </li>
      ))}
    </ul>
  );
}

/** What a contract found: how many events kept it, every way the rest broke it — expected
 * against received — and the events it refused, which exist nowhere else. */
export function ContractReport({ contract, since }: { contract: EventContract; since: string }) {
  const report = contract.report;
  if (!report) return null;
  const refused = contract.rejected;

  return (
    <div className="space-y-5">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatTile label={`Events · ${since}`} value={formatNumber(report.events)} />
        <StatTile
          label="Checked"
          value={formatNumber(report.checked)}
          hint={report.events > report.checked ? "the rest arrived before the contract" : undefined}
        />
        <StatTile
          label="Breaking it"
          value={formatNumber(report.violating)}
          tone={report.violating ? "danger" : "default"}
          hint={
            report.checked
              ? `${Math.round((report.violating / report.checked) * 100)}% of checked`
              : undefined
          }
        />
        <StatTile
          label="Refused · all time"
          value={formatNumber(refused.count)}
          tone={refused.count ? "danger" : "default"}
          hint={refused.last_at ? `last ${formatRelative(refused.last_at)}` : undefined}
        />
      </div>

      <div className="border border-border">
        <p className="label-strong border-b border-border px-4 py-3">How events broke it</p>
        {report.violations.length === 0 ? (
          <p className="px-4 py-4 text-xs text-muted-foreground">
            {report.checked
              ? "Every event checked in this window kept the contract."
              : "No event of this type was checked in this window."}
          </p>
        ) : (
          <Table className="min-w-[760px]">
            <THead>
              <TR>
                <TH className="w-48">Field</TH>
                <TH className="w-32">Rule</TH>
                <TH>Expected</TH>
                <TH>Received (latest)</TH>
                <TH className="w-20 text-right">Events</TH>
                <TH className="w-28">Last seen</TH>
              </TR>
            </THead>
            <tbody>
              {report.violations.map((item) => (
                <TR key={`${item.path}-${item.rule}-${item.expected}`}>
                  <TD className="font-mono text-[11px] text-foreground">{item.path}</TD>
                  <TD>
                    <Badge className="whitespace-nowrap border-danger/50 text-danger">
                      {RULES[item.rule] ?? item.rule}
                    </Badge>
                  </TD>
                  <TD className="font-mono text-[11px]">{item.expected ?? "—"}</TD>
                  <TD className="font-mono text-[11px] text-danger">{item.received ?? "—"}</TD>
                  <TD className="numeric text-right text-xs">{formatNumber(item.events)}</TD>
                  <TD className="label whitespace-nowrap">{formatRelative(item.last_seen_at)}</TD>
                </TR>
              ))}
            </tbody>
          </Table>
        )}
      </div>

      {report.recent.length > 0 && (
        <div className="border border-border">
          <p className="label-strong border-b border-border px-4 py-3">
            Latest events that broke it
          </p>
          <ul>
            {report.recent.map((item) => (
              <li
                key={item.event_id}
                className="grid gap-2 border-t border-border px-4 py-2.5 first:border-t-0 md:grid-cols-[9rem_14rem_1fr]"
              >
                <span className="label whitespace-nowrap">{formatDate(item.received_at)}</span>
                <span className="min-w-0 truncate font-mono text-[11px]">
                  <Link
                    href={`/customers/${encodeURIComponent(item.customer_id)}`}
                    className="hover:text-accent"
                  >
                    {item.customer_id}
                  </Link>
                  {item.external_event_id && (
                    <span className="text-muted-foreground"> · {item.external_event_id}</span>
                  )}
                </span>
                <Violations items={item.violations} />
              </li>
            ))}
          </ul>
        </div>
      )}

      {refused.recent.length > 0 && (
        <div className="border border-danger/30">
          <div className="border-b border-danger/30 px-4 py-3">
            <p className="label-strong text-danger">Refused</p>
            <p className="mt-1 text-xs text-muted-foreground">
              Refused events were never stored, so they appear only here — the latest{" "}
              {refused.recent.length}.
            </p>
          </div>
          <ul>
            {refused.recent.map((item, index) => (
              <li
                key={`${item.at}-${index}`}
                className="grid gap-2 border-t border-border px-4 py-2.5 first:border-t-0 md:grid-cols-[9rem_14rem_1fr]"
              >
                <span className="label whitespace-nowrap">{formatDate(item.at)}</span>
                <span className="min-w-0 truncate font-mono text-[11px]">
                  {item.customer_id}
                  {item.external_event_id && (
                    <span className="text-muted-foreground"> · {item.external_event_id}</span>
                  )}
                </span>
                <Violations items={item.violations} />
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
