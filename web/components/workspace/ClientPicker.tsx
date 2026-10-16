// Consultant mode: "Working on" — pick the business or person to work on today.
//
// Shown at the top of the workspace's left pane, in lavender, only for
// consultants. The list is GET /api/consultant/clients (only the clients
// assigned to this consultant), in a searchable dropdown: click it to see the
// WHOLE list, or type part of a business name, a person's name, an email, a
// location or what they need help with. With two or more clients, three small
// filters narrow the list first:
//   * Help with — the projects clients picked on "Your needs" (Dashboards,
//     Machine Learning...), each with how many clients want it;
//   * One-time / Monthly — how they want help;
//   * Sort — A to Z, or recently active (last sign-in first).
// The client you're working on always stays in the list. Choosing one makes every signed-in request
// carry X-Acting-As, so the workspace shows THAT client's plan, and downloads
// are billed to them. "My own workspace" is the consultant's own account,
// with every service on — handy for testing.
//
// "Add a client" creates an account for someone who'd rather not sign up
// themselves (POST /api/consultant/clients), assigned to this consultant —
// refused when the consultant is at their capacity. The fill bar under the
// list is how full I am (my capacity is in Settings). Each client shows how
// they want help: one-time analysis or monthly.

"use client";

import { useMemo, useState, type FormEvent } from "react";
import { addClient } from "@/lib/api";
import type { AccountType, ConsultantClient, ConsultantLoad, ConsultantPlan, Industry, IntakeOptions, Location, Service } from "@/lib/types";
import { LoadBar, PLAN_LABELS } from "@/components/consultants/ConsultantPicker";
import { capitalizeFirst, companyNameError, emailError, personNameError } from "@/lib/validation";
import LocationPicker from "@/components/auth/LocationPicker";
import IndustryPicker from "@/components/wizard/IndustryPicker";
import SearchSelect from "@/components/ui/SearchSelect";
import { Button, ErrorBox } from "@/components/wizard/ui";

const field = "w-full rounded-xl border border-line bg-surface px-3 py-2 text-sm outline-none focus:border-consultant";
const small = "w-full rounded-lg border border-line bg-surface px-2 py-1 text-xs outline-none focus:border-consultant";

export default function ClientPicker(props: {
  clients: ConsultantClient[];
  actingId: string | null;
  onChoose: (id: string | null) => void;
  onAddClient: () => void;
  /** How full I am (null while loading). */
  load: ConsultantLoad | null;
  /** The service catalog, for the "Help with" names. */
  services?: Service[];
}) {
  const current = props.clients.find((c) => c.id === props.actingId);
  const [helpWith, setHelpWith] = useState("");                      // "" = any
  const [plan, setPlan] = useState<ConsultantPlan | "">("");          // "" = both
  const [sort, setSort] = useState<"name" | "recent">("name");
  const serviceName = (id: string) => props.services?.find((s) => s.id === id)?.name ?? id;

  // "Help with" choices: only projects some client picked, each with how many clients picked it.
  const interests = useMemo(() => {
    const counts = new Map<string, number>();
    for (const c of props.clients) for (const id of c.interested_services ?? []) counts.set(id, (counts.get(id) ?? 0) + 1);
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [props.clients]);

  const shown = useMemo(() => {
    const kept = props.clients.filter(
      (c) =>
        c.id === props.actingId || // the client you're working on always stays
        ((!helpWith || (c.interested_services ?? []).includes(helpWith)) && (!plan || c.consultant_plan === plan)),
    );
    return [...kept].sort((a, b) =>
      sort === "recent"
        ? (b.last_login_at ?? "").localeCompare(a.last_login_at ?? "") || a.label.localeCompare(b.label)
        : a.label.localeCompare(b.label),
    );
  }, [props.clients, props.actingId, helpWith, plan, sort]);
  const filtering = !!helpWith || !!plan;

  return (
    <div className="mb-3 rounded-2xl border border-consultant/40 bg-consultant-soft p-3">
      <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-consultant">Working on</p>
      {props.clients.length >= 2 && (
        <div className="mb-2 space-y-1.5">
          {interests.length > 0 && (
            <select aria-label="Filter by what they need help with" className={small} value={helpWith}
              onChange={(e) => setHelpWith(e.target.value)}>
              <option value="">Help with: anything</option>
              {interests.map(([id, n]) => (
                <option key={id} value={id}>
                  {serviceName(id)} ({n})
                </option>
              ))}
            </select>
          )}
          <div className="grid grid-cols-2 gap-1.5">
            <select aria-label="Filter by one-time or monthly" className={small} value={plan}
              onChange={(e) => setPlan(e.target.value as ConsultantPlan | "")}>
              <option value="">One-time &amp; monthly</option>
              <option value="one_time">{PLAN_LABELS.one_time.title}</option>
              <option value="monthly">{PLAN_LABELS.monthly.title}</option>
            </select>
            <select aria-label="Sort clients" className={small} value={sort}
              onChange={(e) => setSort(e.target.value as "name" | "recent")}>
              <option value="name">A to Z</option>
              <option value="recent">Recently active</option>
            </select>
          </div>
        </div>
      )}
      <SearchSelect
        label="Working on"
        accent="consultant"
        placeholder="Choose a business or person…"
        value={props.actingId}
        onChange={props.onChoose}
        options={shown.map((c) => {
          const help = (c.interested_services ?? []).map(serviceName);
          return {
            value: c.id,
            label: c.label,
            detail: [
              c.company_name ? `${c.person} · ${c.email}` : c.email,
              c.consultant_plan && PLAN_LABELS[c.consultant_plan].title,
              help.length ? `Help with: ${help.join(", ")}` : null,
            ]
              .filter(Boolean)
              .join(" · "),
            // Typing also finds clients by location, industry, or what they need help with.
            keywords: `${c.person} ${c.email} ${c.location ?? ""} ${c.industry_other ?? ""} ${help.join(" ")}`,
          };
        })}
      />
      {filtering && (
        <p className="mt-1 text-xs text-ink-muted">
          Showing {shown.length} of {props.clients.length} ·{" "}
          <button type="button" className="font-semibold text-consultant hover:underline"
            onClick={() => { setHelpWith(""); setPlan(""); }}>
            Show all
          </button>
        </p>
      )}
      <p className="mt-1.5 text-xs text-ink-muted">
        {current
          ? `${current.person} · ${current.email}${current.consultant_plan ? ` · ${PLAN_LABELS[current.consultant_plan].title}` : ""}`
          : props.clients.length
            ? "None chosen: this is your own workspace (every service, for testing)."
            : "No clients yet. Add one, or they'll appear here when they ask for a consultant."}
      </p>
      {props.load && (
        <div className="mt-2">
          <LoadBar load={props.load} />
        </div>
      )}
      <button type="button" onClick={props.onAddClient} className="mt-2 text-xs font-semibold text-consultant hover:underline">
        + Add a client
      </button>
    </div>
  );
}

/** Create an account for a client (assigned to this consultant). */
export function AddClientForm(props: {
  token: string;
  options: IntakeOptions | null;
  onDone: (client: ConsultantClient) => void;
  onCancel: () => void;
}) {
  const [type, setType] = useState<AccountType>("business");
  const [first, setFirst] = useState("");
  const [last, setLast] = useState("");
  const [email, setEmail] = useState("");
  const [company, setCompany] = useState("");
  const [industry, setIndustry] = useState<Industry>({
    industry_supersector: null,
    naics_sector: null,
    naics_subsector: null,
    company_size: null,
    industry_other: null,
  });
  const [location, setLocation] = useState<Location | null>(null);
  const [submitted, setSubmitted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const errors = {
    first: personNameError(first, "First name"),
    last: personNameError(last, "Last name"),
    email: emailError(email),
    company: type === "business" ? companyNameError(company) : null,
  };
  const bad = Object.values(errors).some(Boolean);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setSubmitted(true);
    if (bad) return;
    setBusy(true);
    setError(null);
    try {
      const client = await addClient(props.token, {
        account_type: type,
        first_name: first.trim(),
        last_name: last.trim(),
        email: email.trim(),
        ...(location ? { location_id: location.id } : {}),
        ...(type === "business" ? { company_name: company.trim(), ...industry } : {}),
      });
      props.onDone(client);
    } catch (err) {
      setError(err instanceof Error ? err.message : "We couldn't add that client.");
    } finally {
      setBusy(false);
    }
  }

  const msg = (k: keyof typeof errors) =>
    submitted && errors[k] ? <span className="block text-xs text-bad">{errors[k]}</span> : null;

  return (
    <form onSubmit={submit} noValidate className="space-y-4 rounded-2xl border border-consultant/40 bg-surface p-5 shadow-[var(--shadow-card)]">
      <div>
        <h2 className="text-lg font-semibold">Add a client</h2>
        <p className="text-sm text-ink-muted">
          For clients who&apos;d rather not sign up themselves. The account is assigned to you, with every tool on. They
          can sign in with this email anytime.
        </p>
      </div>
      <div className="flex gap-2">
        {(["business", "individual"] as const).map((t) => (
          <button
            key={t}
            type="button"
            aria-pressed={type === t}
            onClick={() => setType(t)}
            className={`rounded-full border px-4 py-1.5 text-sm ${type === t ? "border-consultant bg-consultant-soft font-semibold" : "border-line"}`}
          >
            {t === "business" ? "Business" : "Individual"}
          </button>
        ))}
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="block space-y-1 text-sm">
          <span>First name</span>
          <input className={field} value={first} onChange={(e) => setFirst(capitalizeFirst(e.target.value))} />
          {msg("first")}
        </label>
        <label className="block space-y-1 text-sm">
          <span>Last name</span>
          <input className={field} value={last} onChange={(e) => setLast(capitalizeFirst(e.target.value))} />
          {msg("last")}
        </label>
        <label className="block space-y-1 text-sm">
          <span>Email address</span>
          <input type="email" className={field} value={email} onChange={(e) => setEmail(e.target.value)} />
          {msg("email")}
        </label>
        {type === "business" && (
          <label className="block space-y-1 text-sm">
            <span>Business name</span>
            <input className={field} value={company} onChange={(e) => setCompany(e.target.value)} />
            {msg("company")}
          </label>
        )}
      </div>
      {type === "business" && props.options && (
        <IndustryPicker industries={props.options.industries} value={industry} onChange={setIndustry} />
      )}
      <div className="space-y-1 text-sm">
        <span>
          Location <span className="text-ink-muted">(optional)</span>
        </span>
        <LocationPicker value={location} onChange={setLocation} />
      </div>
      <ErrorBox message={error} />
      <div className="flex gap-2">
        <button
          type="submit"
          disabled={busy}
          className="rounded-full bg-consultant px-5 py-2 text-sm font-semibold text-on-consultant disabled:opacity-50"
        >
          {busy ? "Adding…" : "Add client"}
        </button>
        <Button variant="secondary" onClick={props.onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
