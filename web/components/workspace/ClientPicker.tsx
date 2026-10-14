// Consultant mode: "Working on" — pick the business or person to work on today.
//
// Shown at the top of the workspace's left pane, in lavender, only for
// consultants. The list is GET /api/consultant/clients (only the clients
// assigned to this consultant), in a searchable dropdown: type part of a
// business name, a person's name or an email. Choosing one makes every signed-in request
// carry X-Acting-As, so the workspace shows THAT client's plan, and downloads
// are billed to them. "My own workspace" is the consultant's own account,
// with every service on — handy for testing.
//
// "Add a client" creates an account for someone who'd rather not sign up
// themselves (POST /api/consultant/clients), assigned to this consultant.

"use client";

import { useState, type FormEvent } from "react";
import { addClient } from "@/lib/api";
import type { AccountType, ConsultantClient, Industry, IntakeOptions, Location } from "@/lib/types";
import { capitalizeFirst, companyNameError, emailError, personNameError } from "@/lib/validation";
import LocationPicker from "@/components/auth/LocationPicker";
import IndustryPicker from "@/components/wizard/IndustryPicker";
import SearchSelect from "@/components/ui/SearchSelect";
import { Button, ErrorBox } from "@/components/wizard/ui";

const field = "w-full rounded-xl border border-line bg-surface px-3 py-2 text-sm outline-none focus:border-consultant";

export default function ClientPicker(props: {
  clients: ConsultantClient[];
  actingId: string | null;
  onChoose: (id: string | null) => void;
  onAddClient: () => void;
}) {
  const current = props.clients.find((c) => c.id === props.actingId);

  return (
    <div className="mb-3 rounded-2xl border border-consultant/40 bg-consultant-soft p-3">
      <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-consultant">Working on</p>
      <SearchSelect
        label="Working on"
        accent="consultant"
        placeholder="Choose a business or person…"
        value={props.actingId}
        onChange={props.onChoose}
        options={props.clients.map((c) => ({
          value: c.id,
          label: c.label,
          detail: c.company_name ? `${c.person} · ${c.email}` : c.email,
          keywords: `${c.person} ${c.email} ${c.location ?? ""} ${c.industry_other ?? ""}`,
        }))}
      />
      <p className="mt-1.5 text-xs text-ink-muted">
        {current
          ? `${current.person} · ${current.email}`
          : props.clients.length
            ? "None chosen: this is your own workspace (every service, for testing)."
            : "No clients yet. Add one, or they'll appear here when they ask for a consultant."}
      </p>
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
