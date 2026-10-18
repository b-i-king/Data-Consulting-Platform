// Settings -> Account -> "Your team" (business accounts only).
//
// Everyone on the team sees who's on it and each person's role:
//   Owner (admin)       holds the plan; billing, people and roles
//   Developer           uploads, connects and loads data, edits dashboards
//   Member (view only)  sees and downloads everything, changes nothing
// The OWNER also gets: a role dropdown per colleague, "Remove", and "Add a
// person" (Developer by default). A team holds 5 people, owner included
// (TEAM_MAX_PEOPLE); when it's full, "Talk to us about Enterprise" links to a
// call. Server side: api/services/teams.py (it enforces every rule again).

"use client";

import { useEffect, useState } from "react";
import { addTeamMember, getTeam, removeTeamMember, setTeamRole } from "@/lib/api";
import type { Account, TeamInfo, TeamRole } from "@/lib/types";
import { emailError, personNameError } from "@/lib/validation";
import { Button, Card, ConfirmDialog, ErrorBox } from "@/components/wizard/ui";

type ColleagueRole = Exclude<TeamRole, "owner">;
const field = "rounded-xl border border-line bg-surface px-3 py-2 text-sm outline-none focus:border-brand";

export default function TeamPanel({ token, bookingUrl }: { token: string; bookingUrl: string }) {
  const [team, setTeam] = useState<TeamInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [removing, setRemoving] = useState<Account | null>(null);
  // The "Add a person" form.
  const [first, setFirst] = useState("");
  const [last, setLast] = useState("");
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<ColleagueRole>("developer");

  const reload = () =>
    getTeam(token)
      .then(setTeam)
      .catch((e) => setError(e instanceof Error ? e.message : "We couldn't load your team."));
  useEffect(() => {
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  /** Run one change, then reload the team (the server is the source of truth). */
  async function run(key: string, change: () => Promise<unknown>) {
    setBusy(key);
    setError(null);
    try {
      await change();
      await reload();
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : "That didn't work.");
      return false;
    } finally {
      setBusy(null);
    }
  }

  async function add() {
    const problem = personNameError(first, "First name") ?? personNameError(last, "Last name") ?? emailError(email);
    if (problem) return setError(problem);
    const ok = await run("add", () =>
      addTeamMember(token, { first_name: first.trim(), last_name: last.trim(), email: email.trim(), role }),
    );
    if (ok) {
      setFirst("");
      setLast("");
      setEmail("");
      setRole("developer");
    }
  }

  if (!team) return error ? <ErrorBox message={error} /> : null;
  const full = team.seats.used >= team.seats.limit;

  return (
    <Card>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="font-semibold">Your team{team.name ? `: ${team.name}` : ""}</h2>
        <span className="font-mono text-sm text-ink-muted">
          {team.seats.used} of {team.seats.limit} people
        </span>
      </div>
      <p className="mt-1 text-sm text-ink-muted">
        Developers upload, connect and load data. Members (view only) see and download everything but change nothing.
        {team.can_manage ? " Only you, the owner, handle billing and people." : ""}
      </p>
      <ErrorBox message={error} />

      <ul className="mt-3 divide-y divide-line">
        {team.members.map((m) => (
          <li key={m.id} className="flex flex-col gap-2 py-3 text-sm sm:flex-row sm:items-center sm:justify-between">
            <div className="min-w-0">
              <span className="font-medium">
                {m.first_name} {m.last_name}
              </span>
              {m.status === "invited" && <span className="ml-2 text-xs text-ink-muted">(hasn&apos;t signed in yet)</span>}
              <span className="block truncate text-xs text-ink-muted">{m.email}</span>
            </div>
            {m.team_role === "owner" || !team.can_manage ? (
              <span className="text-xs font-semibold text-ink-muted">{team.roles[m.team_role ?? "member"]}</span>
            ) : (
              <div className="flex items-center gap-2">
                <select
                  className={field}
                  value={m.team_role ?? "member"}
                  disabled={busy !== null}
                  aria-label={`Role for ${m.first_name}`}
                  onChange={(e) => run(m.id, () => setTeamRole(token, m.id, e.target.value as ColleagueRole))}
                >
                  <option value="developer">{team.roles.developer}</option>
                  <option value="member">{team.roles.member}</option>
                </select>
                <Button variant="secondary" className="px-4 py-1" disabled={busy !== null} onClick={() => setRemoving(m)}>
                  Remove
                </Button>
              </div>
            )}
          </li>
        ))}
      </ul>

      {team.can_manage &&
        (full ? (
          <p className="mt-3 rounded-xl border border-line bg-surface p-3 text-sm">
            Your team has {team.seats.limit} people, the most a package includes. Need more people?{" "}
            <a href={bookingUrl} target="_blank" rel="noreferrer" className="text-brand hover:underline">
              Talk to us about an Enterprise plan
            </a>
            .
          </p>
        ) : (
          <div className="mt-3 space-y-2">
            <h3 className="text-sm font-medium">Add a person</h3>
            <div className="grid gap-2 sm:grid-cols-[1fr_1fr_1.5fr_auto_auto]">
              <input className={field} placeholder="First name" aria-label="First name" value={first}
                onChange={(e) => setFirst(e.target.value)} />
              <input className={field} placeholder="Last name" aria-label="Last name" value={last}
                onChange={(e) => setLast(e.target.value)} />
              <input className={field} type="email" placeholder="Email" aria-label="Email" value={email}
                onChange={(e) => setEmail(e.target.value)} />
              <select className={field} value={role} aria-label="Role" onChange={(e) => setRole(e.target.value as ColleagueRole)}>
                <option value="developer">{team.roles.developer}</option>
                <option value="member">{team.roles.member}</option>
              </select>
              <Button onClick={add} disabled={busy !== null}>
                {busy === "add" ? "Adding…" : "Add"}
              </Button>
            </div>
            <p className="text-xs text-ink-muted">
              They sign in with this email; the password they type the first time becomes theirs.
            </p>
          </div>
        ))}

      {removing && (
        <ConfirmDialog
          title={`Remove ${removing.first_name} from the team?`}
          confirmLabel={busy ? "Removing…" : "Remove"}
          onConfirm={async () => {
            if (busy) return;
            if (await run(removing.id, () => removeTeamMember(token, removing.id))) setRemoving(null);
          }}
          onCancel={() => setRemoving(null)}
        >
          <p>
            Their account is deleted and they can&apos;t sign in anymore. Your business&apos;s data, dashboards and
            package stay as they are.
          </p>
        </ConfirmDialog>
      )}
    </Card>
  );
}
