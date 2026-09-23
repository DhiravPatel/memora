"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input, Label } from "@/components/ui/input";
import { api, tokenStore } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const result = await api<{ tokens: { access_token: string; refresh_token: string } }>(
        "/v1/auth/login",
        { method: "POST", body: { email, password }, auth: false },
      );
      tokenStore.set(result.tokens);
      router.push("/overview");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center px-4">
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
              <p className="label mb-2">Dashboard</p>
              <h1 className="display-lg">Sign in</h1>
            </div>
            <form onSubmit={handleSubmit} className="space-y-3">
              <div>
                <Label htmlFor="email">Email</Label>
                <Input
                  id="email"
                  type="email"
                  autoComplete="email"
                  required
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                />
              </div>
              <div>
                <Label htmlFor="password">Password</Label>
                <Input
                  id="password"
                  type="password"
                  autoComplete="current-password"
                  required
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                />
              </div>
              {error && <p className="font-mono text-[11px] text-danger">{error}</p>}
              <Button type="submit" className="w-full" loading={loading}>
                Sign in
              </Button>
            </form>
            <p className="label text-center">
              No account?{" "}
              <Link href="/signup" className="text-accent hover:underline">
                Create one
              </Link>
            </p>
          </CardContent>
        </Card>
      </div>
    </main>
  );
}
