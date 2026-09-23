"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input, Label } from "@/components/ui/input";
import { api, tokenStore } from "@/lib/api";

export default function SignupPage() {
  const router = useRouter();
  const [form, setForm] = useState({
    organization_name: "",
    name: "",
    email: "",
    password: "",
  });
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
        "/v1/auth/signup",
        { method: "POST", body: form, auth: false },
      );
      tokenStore.set(result.tokens);
      router.push("/settings");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Signup failed.");
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
              <p className="label mb-2">Get started</p>
              <h1 className="display-lg">Create your account</h1>
              <p className="mt-2 text-xs text-muted-foreground">
                You get a project and an API key immediately after.
              </p>
            </div>
            <form onSubmit={handleSubmit} className="space-y-3">
              <div>
                <Label htmlFor="organization">Organization</Label>
                <Input
                  id="organization"
                  required
                  value={form.organization_name}
                  onChange={update("organization_name")}
                />
              </div>
              <div>
                <Label htmlFor="name">Your name</Label>
                <Input id="name" value={form.name} onChange={update("name")} />
              </div>
              <div>
                <Label htmlFor="email">Email</Label>
                <Input id="email" type="email" required value={form.email} onChange={update("email")} />
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
                Create account
              </Button>
            </form>
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
