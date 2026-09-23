"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label, Select } from "@/components/ui/input";
import { EmptyState, ErrorState, LoadingRow, PageHeader } from "@/components/ui/states";
import { Table, TD, TH, THead, TR } from "@/components/ui/table";
import { useSession } from "@/hooks/use-session";
import { api } from "@/lib/api";
import { formatRelative } from "@/lib/format";
import { cn, INVITATION_COLORS, ROLE_COLORS } from "@/lib/utils";
import type { Invitation, InvitationWithToken, Member } from "@/lib/types";

const ROLES = ["viewer", "member", "admin", "owner"] as const;

/** The accept link, against the dashboard's own origin.
 *
 * It resolves to a page in *this* app, not to the API — pointing it at the API origin
 * produced a link that 404s, which is hard to notice when the only person who clicks it is
 * somebody who was never going to tell you.
 */
function acceptUrl(acceptPath: string): string {
  const origin = typeof window === "undefined" ? "" : window.location.origin;
  return `${origin}${acceptPath}`;
}

export default function TeamPage() {
  const { user } = useSession();
  const queryClient = useQueryClient();
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("member");
  const [invite, setInvite] = useState<InvitationWithToken | null>(null);

  const members = useQuery({
    queryKey: ["members"],
    queryFn: () => api<Member[]>("/v1/organization/members"),
  });

  const invitations = useQuery({
    queryKey: ["invitations"],
    queryFn: () => api<Invitation[]>("/v1/organization/invitations"),
  });

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["members"] });
    queryClient.invalidateQueries({ queryKey: ["invitations"] });
  };

  const sendInvite = useMutation({
    mutationFn: () =>
      api<InvitationWithToken>("/v1/organization/invitations", {
        method: "POST",
        body: { email, role },
      }),
    onSuccess: (created) => {
      setInvite(created);
      setEmail("");
      refresh();
    },
  });

  const changeRole = useMutation({
    mutationFn: ({ userId, nextRole }: { userId: string; nextRole: string }) =>
      api(`/v1/organization/members/${userId}`, { method: "PATCH", body: { role: nextRole } }),
    onSuccess: refresh,
  });

  const removeMember = useMutation({
    mutationFn: (userId: string) => api(`/v1/organization/members/${userId}`, { method: "DELETE" }),
    onSuccess: refresh,
  });

  const revokeInvite = useMutation({
    mutationFn: (invitationId: string) =>
      api(`/v1/organization/invitations/${invitationId}`, { method: "DELETE" }),
    onSuccess: refresh,
  });

  const error =
    sendInvite.error ?? changeRole.error ?? removeMember.error ?? revokeInvite.error ?? null;

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="System"
        title="Team"
        description="Roles are hierarchical: owner > admin > member > viewer. An organization can never lose its last owner."
      />

      {invite && (
        <Card className="border-accent/60 bg-accent-soft shadow-[0_0_0_4px_hsl(var(--accent)/0.08)]">
          <CardHeader>
            <CardTitle className="text-accent">Invitation for {invite.email}</CardTitle>
            <CardDescription>
              {invite.email_queued
                ? "An email is on its way with this link. It is single-use and expires "
                : "Mail could not be queued, so no email will arrive — send this link yourself. It is single-use and expires "}
              {formatRelative(invite.expires_at)}. Only its hash is stored here.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <code className="block break-all border border-border bg-surface-2 p-3 font-mono text-xs text-accent">
              {acceptUrl(invite.accept_path)}
            </code>
            <div className="flex gap-2">
              <Button
                size="sm"
                onClick={() => navigator.clipboard.writeText(acceptUrl(invite.accept_path))}
              >
                Copy link
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setInvite(null)}>
                Done
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Invite someone</CardTitle>
          <CardDescription>You cannot invite at a role above your own.</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap items-end gap-3">
          <div className="min-w-[260px] flex-1">
            <Label htmlFor="invite-email">Email</Label>
            <Input
              id="invite-email"
              type="email"
              placeholder="person@company.com"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
            />
          </div>
          <div className="w-44">
            <Label htmlFor="invite-role">Role</Label>
            <Select id="invite-role" value={role} onChange={(event) => setRole(event.target.value)}>
              {ROLES.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </Select>
          </div>
          <Button
            onClick={() => sendInvite.mutate()}
            loading={sendInvite.isPending}
            disabled={!email.includes("@")}
          >
            Send invite
          </Button>
        </CardContent>
      </Card>

      {error && <ErrorState error={error} />}

      <Card>
        <CardHeader>
          <CardTitle>Members</CardTitle>
        </CardHeader>
        {members.isLoading && <LoadingRow />}
        {members.error && <ErrorState error={members.error} />}
        {!!members.data?.length && (
          <Table className="min-w-[860px]">
            <THead>
              <TR>
                <TH>Member</TH>
                <TH className="w-40">Role</TH>
                <TH className="w-32">Status</TH>
                <TH className="w-36">Last login</TH>
                <TH className="w-24 text-right" />
              </TR>
            </THead>
            <tbody>
              {members.data.map((member) => (
                <TR key={member.id} className={cn(!member.is_active && "opacity-50")}>
                  <TD>
                    <p className="text-sm font-semibold">{member.name || member.email}</p>
                    <p className="label mt-0.5">{member.email}</p>
                  </TD>
                  <TD>
                    <Select
                      value={member.role}
                      disabled={member.id === user?.id}
                      onChange={(event) =>
                        changeRole.mutate({ userId: member.id, nextRole: event.target.value })
                      }
                      className="h-8 w-32"
                    >
                      {ROLES.map((item) => (
                        <option key={item} value={item}>
                          {item}
                        </option>
                      ))}
                    </Select>
                  </TD>
                  <TD>
                    <Badge className={member.is_active ? ROLE_COLORS[member.role] : ""}>
                      {member.is_active ? "active" : "removed"}
                    </Badge>
                  </TD>
                  <TD className="label whitespace-nowrap">
                    {formatRelative(member.last_login_at)}
                  </TD>
                  <TD className="text-right">
                    {member.is_active && member.id !== user?.id && (
                      <Button
                        size="sm"
                        variant="ghost"
                        loading={removeMember.isPending && removeMember.variables === member.id}
                        onClick={() => removeMember.mutate(member.id)}
                      >
                        Remove
                      </Button>
                    )}
                  </TD>
                </TR>
              ))}
            </tbody>
          </Table>
        )}
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Invitations</CardTitle>
        </CardHeader>
        {invitations.isLoading && <LoadingRow />}
        {invitations.data?.length === 0 && <EmptyState title="No invitations" />}
        {!!invitations.data?.length && (
          <Table>
            <THead>
              <TR>
                <TH>Email</TH>
                <TH className="w-28">Role</TH>
                <TH className="w-28">Status</TH>
                <TH className="w-36">Expires</TH>
                <TH className="w-24 text-right" />
              </TR>
            </THead>
            <tbody>
              {invitations.data.map((invitation) => (
                <TR key={invitation.id}>
                  <TD className="font-mono text-[11px]">{invitation.email}</TD>
                  <TD>
                    <Badge className={ROLE_COLORS[invitation.role] ?? ""}>{invitation.role}</Badge>
                  </TD>
                  <TD>
                    <Badge className={INVITATION_COLORS[invitation.status] ?? ""}>
                      {invitation.status}
                    </Badge>
                  </TD>
                  <TD className="label whitespace-nowrap">
                    {formatRelative(invitation.expires_at)}
                  </TD>
                  <TD className="text-right">
                    {invitation.status === "pending" && (
                      <Button
                        size="sm"
                        variant="ghost"
                        loading={revokeInvite.isPending && revokeInvite.variables === invitation.id}
                        onClick={() => revokeInvite.mutate(invitation.id)}
                      >
                        Revoke
                      </Button>
                    )}
                  </TD>
                </TR>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </div>
  );
}
