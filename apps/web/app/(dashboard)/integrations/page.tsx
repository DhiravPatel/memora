"use client";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PageHeader } from "@/components/ui/states";
import { useSession } from "@/hooks/use-session";
import { API_URL } from "@/lib/api";
import { titleCase } from "@/lib/format";

const PROVIDERS = [
  { id: "stripe", header: "Stripe-Signature", blurb: "Subscriptions, invoices, refunds, disputes" },
  { id: "intercom", header: "X-Hub-Signature", blurb: "Conversations and replies" },
  { id: "zendesk", header: "X-Zendesk-Webhook-Signature", blurb: "Tickets by status" },
  { id: "hubspot", header: "X-HubSpot-Signature", blurb: "Contact and deal changes" },
  { id: "posthog", header: "X-Posthog-Signature", blurb: "Product events, noise filtered" },
  { id: "slack", header: "X-Slack-Signature", blurb: "Channel messages, bots ignored" },
  { id: "generic", header: "X-Signature", blurb: "Signed passthrough for internal systems" },
];

export default function IntegrationsPage() {
  const { project } = useSession();
  const settings = (project?.settings ?? {}) as Record<string, any>;
  const configured = (settings.integrations ?? {}) as Record<string, { signing_secret?: string }>;

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="System"
        title="Integrations"
        description="Point a provider's webhook here. Each payload is signature-verified, normalised into the internal event schema, and ingested through the same path as the events API."
      />

      <div className="grid gap-px border border-border bg-border md:grid-cols-2">
        {PROVIDERS.map((provider) => {
          const ready = Boolean(configured[provider.id]?.signing_secret);
          return (
            <Card key={provider.id} className="border-0 bg-surface">
              <CardHeader className="flex items-start justify-between gap-3">
                <div>
                  <CardTitle className="font-display text-[17px] normal-case tracking-[-0.01em]">
                    {titleCase(provider.id)}
                  </CardTitle>
                  <p className="mt-1 text-xs text-muted-foreground">{provider.blurb}</p>
                </div>
                <Badge
                  className={
                    ready ? "border-success/70 bg-success/10 text-success" : "border-border bg-surface-2"
                  }
                >
                  {ready ? "ready" : "not configured"}
                </Badge>
              </CardHeader>
              <CardContent className="space-y-2.5">
                <div>
                  <p className="label mb-1">Webhook URL</p>
                  <code className="block break-all border border-border bg-surface-2 px-2 py-1.5 font-mono text-[10px] text-foreground">
                    {API_URL}/v1/integrations/{provider.id}/webhook
                  </code>
                </div>
                <div className="flex flex-wrap gap-4">
                  <div>
                    <p className="label mb-1">Signature header</p>
                    <p className="font-mono text-[10px]">{provider.header}</p>
                  </div>
                  <div>
                    <p className="label mb-1">Secret setting</p>
                    <p className="font-mono text-[10px]">
                      integrations.{provider.id}.signing_secret
                    </p>
                  </div>
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>
    </div>
  );
}
