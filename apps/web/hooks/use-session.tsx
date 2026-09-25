"use client";

import { useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { api, projectStore, tokenStore } from "@/lib/api";
import type { Project } from "@/lib/types";

interface SessionUser {
  id: string;
  email: string;
  name: string | null;
  role: string;
  organization_id: string;
}

interface SessionValue {
  user: SessionUser | null;
  isLoading: boolean;
  projects: Project[];
  project: Project | null;
  projectId: string | null;
  selectProject: (projectId: string) => void;
  logout: () => void;
  refreshProjects: () => void;
}

const SessionContext = createContext<SessionValue | null>(null);

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [projectId, setProjectId] = useState<string | null>(null);
  // Follows the token store rather than reading it once: signing in happens on a page this
  // provider has already rendered for.
  const [signedIn, setSignedIn] = useState(false);

  useEffect(() => {
    setProjectId(projectStore.get());
    const sync = () => setSignedIn(Boolean(tokenStore.access));
    sync();
    return tokenStore.subscribe(sync);
  }, []);

  const userQuery = useQuery({
    queryKey: ["me"],
    queryFn: () => api<SessionUser>("/v1/auth/me"),
    retry: false,
    enabled: signedIn,
  });

  const projectsQuery = useQuery({
    queryKey: ["projects"],
    queryFn: () => api<Project[]>("/v1/projects"),
    enabled: Boolean(userQuery.data),
  });

  const projects = useMemo(() => projectsQuery.data ?? [], [projectsQuery.data]);

  // Pick a project automatically so the dashboard is never in a half-configured state.
  useEffect(() => {
    if (!projects.length) return;
    const stored = projectStore.get();
    const valid = stored && projects.some((project) => project.id === stored);
    const next = valid ? stored! : projects[0]!.id;
    if (next !== projectId) {
      projectStore.set(next);
      setProjectId(next);
    }
  }, [projects, projectId]);

  const selectProject = useCallback((id: string) => {
    projectStore.set(id);
    setProjectId(id);
  }, []);

  const logout = useCallback(() => {
    tokenStore.clear();
    projectStore.clear();
    queryClient.clear();
    router.push("/login");
  }, [queryClient, router]);

  const value: SessionValue = {
    user: userQuery.data ?? null,
    isLoading: userQuery.isLoading || projectsQuery.isLoading,
    projects,
    project: projects.find((project) => project.id === projectId) ?? null,
    projectId,
    selectProject,
    logout,
    refreshProjects: () => queryClient.invalidateQueries({ queryKey: ["projects"] }),
  };

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionValue {
  const context = useContext(SessionContext);
  if (!context) throw new Error("useSession must be used inside SessionProvider");
  return context;
}

/** Redirects to /login when there is no valid session. */
export function useRequireSession(): SessionValue {
  const session = useSession();
  const router = useRouter();

  useEffect(() => {
    if (typeof window === "undefined") return;
    if (!tokenStore.access) router.replace("/login");
  }, [router]);

  return session;
}
