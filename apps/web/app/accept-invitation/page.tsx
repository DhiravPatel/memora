"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input, Label } from "@/components/ui/input";
import { api, tokenStore } from "@/lib/api";

/** Where an invitation email lands: set a password, and you are in.
 *
 * The token is single-use and already carries the organization and the role, so this asks
 * for nothing the invitation already knows — a form that made somebody re-type the
 * organization they were invited to would be asking them to confirm a decision that was
 * not theirs.
 */
export default function AcceptInvitationPage() {
  return (
    <Suspense fallback={null}>
      <AcceptInvitation />
    </Suspense>
  );
}

function AcceptInvitation() {
  const router = useRouter();
  const token = useSearchParams().get("token") ?? "";
  const [form, setForm] = useState({ name: "", password: "" });
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  function update(field: keyof typeof form) {
    return (event: React.ChangeEvent<HTMLInputElement>) =>
      setForm((current) => ({ ...current, [field]: event.target.value }));
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const result = await api<{ tokens: { access_token: string; refresh_token: string } }>(
        "/v1/auth/accept-invitation",
        { method: "POST", body: { token, ...form }, auth: false },
      );
      tokenStore.set(result.tokens);
      router.push("/overview");
    } catch (err) {
      setError(err instanceof Error ? err.message : "This invitation could not be accepted.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center px-4 py-10">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex items-center gap-2.5">
          <span className="flex h-8 w-8 items-center justify-center border border-accent bg-accent text-accent-foreground">
            <span className="font-display text-base leading-none">M</span>
          </span>
          <span className="font-display text-lg leading-none tracking-[-0.01em]">
            MEMORY<span className="text-accent">/</span>LAYER
          </span>
        </div>

        <Card className="panel-raised">
          <CardContent className="space-y-5 py-6">
            <div>
              <p className="label mb-2">You were invited</p>
              <h1 className="display-lg">Join your team</h1>
              <p className="mt-2 text-xs text-muted-foreground">
                Set a password and you are in. Your email and role come from the invitation.
              </p>
            </div>

            {token ? (
              <form onSubmit={handleSubmit} className="space-y-3">
                <div>
                  <Label htmlFor="name">Your name</Label>
                  <Input id="name" value={form.name} onChange={update("name")} />
                </div>
                <div>
                  <Label htmlFor="password">Password</Label>
                  <Input
                    id="password"
                    type="password"
                    required
                    minLength={8}
                    value={form.password}
                    onChange={update("password")}
                  />
                  <p className="label mt-1">At least 8 characters</p>
                </div>
                {error && <p className="font-mono text-[11px] text-danger">{error}</p>}
                <Button type="submit" className="w-full" loading={loading}>
                  Accept invitation
                </Button>
              </form>
            ) : (
              // Reached without a token: almost always a link that lost its query string in
              // a mail client. Saying so beats a form that cannot possibly work.
              <p className="border-l-2 border-danger bg-surface-2 px-3 py-2 text-[11px] text-muted-foreground">
                This link is missing its invitation token. Open the link from your invitation email
                again, or ask whoever invited you to send a new one.
              </p>
            )}

            <p className="label text-center">
              Already have an account?{" "}
              <Link href="/login" className="text-accent hover:underline">
                Sign in
              </Link>
            </p>
          </CardContent>
        </Card>
      </div>
    </main>
  );
}
