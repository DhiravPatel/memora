"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/input";
import { useRequireSession } from "@/hooks/use-session";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/overview", label: "Overview", group: "Signal" },
  { href: "/health", label: "Health", group: "Signal" },
  { href: "/signals", label: "Signals", group: "Signal" },
  { href: "/customers", label: "Customers", group: "Memory" },
  { href: "/memories", label: "Memories", group: "Memory" },
  { href: "/entities", label: "Entities", group: "Memory" },
  { href: "/goals", label: "Goals", group: "Memory" },
  { href: "/events", label: "Events", group: "Pipeline" },
  { href: "/playground", label: "Playground", group: "Pipeline" },
  { href: "/agents", label: "Agent Sessions", group: "Pipeline" },
  { href: "/integrations", label: "Integrations", group: "System" },
  { href: "/webhooks", label: "Webhooks", group: "System" },
  { href: "/keys", label: "API Keys", group: "System" },
  { href: "/team", label: "Team", group: "System" },
  { href: "/audit", label: "Audit", group: "System" },
  { href: "/settings", label: "Settings", group: "System" },
];

const GROUPS = ["Signal", "Memory", "Pipeline", "System"];

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { user, projects, projectId, selectProject, logout } = useRequireSession();

  return (
    <div className="flex min-h-screen">
      <aside className="hidden w-64 shrink-0 border-r border-border bg-surface md:flex md:flex-col">
        <Link
          href="/overview"
          className="group flex items-center gap-3 border-b border-border px-5 py-5"
        >
          <span className="flex h-8 w-8 items-center justify-center rounded-md bg-accent text-accent-foreground shadow-press transition-transform duration-200 ease-swift group-hover:-translate-y-px">
            <span className="font-display text-base leading-none">M</span>
          </span>
          <span className="font-display text-lg leading-none tracking-[-0.01em]">
            Memory<span className="text-accent">/</span>Layer
          </span>
        </Link>

        <div className="border-b border-border px-5 py-4">
          <p className="label mb-2">Project</p>
          <Select
            aria-label="Project"
            value={projectId ?? ""}
            onChange={(event) => selectProject(event.target.value)}
            disabled={!projects.length}
          >
            {projects.length === 0 && <option value="">No projects</option>}
            {projects.map((project) => (
              <option key={project.id} value={project.id}>
                {project.name}
              </option>
            ))}
          </Select>
        </div>

        <nav className="flex-1 overflow-y-auto px-3 py-4">
          {GROUPS.map((group) => (
            <div key={group} className="mb-6 last:mb-0">
              <p className="label px-3 pb-2">{group}</p>
              <div className="space-y-0.5">
                {NAV.filter((item) => item.group === group).map((item) => {
                  const active =
                    pathname === item.href || pathname.startsWith(`${item.href}/`);
                  return (
                    <Link
                      key={item.href}
                      href={item.href}
                      className={cn(
                        "relative flex items-center gap-2 rounded-md px-3 py-2 font-mono text-[11px] uppercase tracking-label",
                        "transition-[color,background-color] duration-150 ease-swift",
                        active
                          ? "bg-accent-soft font-medium text-accent"
                          : "text-muted-foreground hover:bg-surface-2 hover:text-foreground",
                      )}
                    >
                      {/* The rail marks the route you are on; hover only tints. */}
                      {active && (
                        <span
                          className="absolute inset-y-1.5 left-0 w-[2px] rounded-r bg-accent"
                          aria-hidden
                        />
                      )}
                      {item.label}
                    </Link>
                  );
                })}
              </div>
            </div>
          ))}
        </nav>

        <div className="border-t border-border px-5 py-4">
          <p className="label">Signed in</p>
          <p className="mt-1.5 truncate font-mono text-[11px] text-foreground">
            {user?.email ?? "—"}
          </p>
          <Button variant="outline" size="sm" className="mt-3 w-full" onClick={logout}>
            Sign out
          </Button>
        </div>
      </aside>

      <main className="flex-1 overflow-x-hidden px-5 py-8 md:px-10 md:py-10">
        <div className="stagger mx-auto max-w-[1400px] space-y-7">{children}</div>
      </main>
    </div>
  );
}
