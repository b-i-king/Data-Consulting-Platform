"use client";

// ── Admin: partner logos to review (Settings -> Administration) ──────────────
// Every logo waits here until an admin decides (api/services/partners.py).
// Shown per application: the logo on a card like the carousel's (switch the
// site's Light / Dark theme to check it shows in both), the website and LinkedIn links to check, and
// the automatic checks. Approve, or turn down with a reason the business sees.
// What to look for: a real logo of a real business, clean and family-friendly
// (nothing vulgar or sexual), and no ads or slogans added.
//
// Also here: ADD a company you work with directly (invoiced outside the
// platform, no account) — name, website and logo, live at once — and the list
// of partners on the home page, each with Remove.

import { useCallback, useEffect, useState } from "react";
import { addPartnerByAdmin, decidePartner, getPartnerReviews, getPrivateLogo, removePartner } from "@/lib/api";
import type { PartnerReview } from "@/lib/types";
import { Button, Card, ErrorBox } from "@/components/wizard/ui";
import ThemedLogo from "@/components/home/ThemedLogo";

const CHECK_LABELS: [keyof PartnerReview["checks"], string][] = [
  ["own_domain", "Own website domain"],
  ["website_reachable", "Website answers"],
  ["email_matches_website", "Account email on the website's domain"],
  ["linkedin_given", "LinkedIn company page given"],
];

/** The logo as sent (its own colors: what you approve) and as the home page will draw it (theme colors). */
function Logo({ token, url, name, width, height }: { token: string; url: string; name: string; width: number; height: number }) {
  const [src, setSrc] = useState<string | null>(null);
  useEffect(() => {
    let made: string | null = null;
    getPrivateLogo(token, url).then((u) => setSrc((made = u))).catch(() => setSrc(null));
    return () => {
      if (made) URL.revokeObjectURL(made);
    };
  }, [token, url]);
  if (!src) return <div className="h-20 w-44 rounded-2xl border border-line" />;
  return (
    <div className="flex gap-2">
      <div className="flex h-20 w-44 items-center justify-center rounded-2xl border border-line bg-surface px-4" title="As sent">
        <ThemedLogo src={src} name={`${name} logo`} width={width} height={height} original />
      </div>
      <div className="group flex h-20 w-44 items-center justify-center rounded-2xl border border-line bg-surface px-4" title="On the home page">
        <ThemedLogo src={src} name={`${name} logo, as shown`} width={width} height={height} />
      </div>
    </div>
  );
}

export default function AdminPartners({ token }: { token: string }) {
  const [all, setAll] = useState<PartnerReview[] | null>(null);
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [website, setWebsite] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [fileKey, setFileKey] = useState(0);   // bumping it clears the file picker after adding
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    getPartnerReviews(token).then((r) => setAll(r.partners)).catch((e) => setError(e instanceof Error ? e.message : "Couldn't load."));
  }, [token]);
  useEffect(load, [load]);
  const rows = all?.filter((p) => p.status === "pending") ?? null;
  const live = all?.filter((p) => p.status === "approved") ?? [];

  async function add() {
    if (!file) return setError("Please choose the logo file.");
    setBusy(true);
    setError(null);
    try {
      await addPartnerByAdmin(token, { name, website, logo: file });
      setName("");
      setWebsite("");
      setFile(null);
      setFileKey((k) => k + 1);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "That didn't go through.");
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: string) {
    setError(null);
    try {
      await removePartner(token, id);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "That didn't go through.");
    }
  }

  async function decide(id: string, approve: boolean) {
    setError(null);
    try {
      await decidePartner(token, id, approve, reasons[id]);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "That didn't go through.");
    }
  }

  return (
    <Card>
      <h3 className="font-semibold">&ldquo;Trusted by&rdquo; logos to review</h3>
      <p className="mt-1 text-sm text-ink-muted">
        Approve only a real business&apos;s clean, family-friendly logo (no vulgar or sexual images, no ads or
        slogans). Approved logos show on the home page while the business&apos;s plan is paid.
      </p>
      {rows && rows.length === 0 && <p className="mt-3 text-sm text-ink-muted">Nothing waiting.</p>}
      <ul className="mt-3 space-y-4">
        {(rows ?? []).map((p) => (
          <li key={p.id} className="rounded-2xl border border-line p-4">
            <div className="flex flex-wrap items-start gap-4">
              <Logo token={token} url={p.logo_url} name={p.business_name} width={p.logo_width} height={p.logo_height} />
              <div className="min-w-0 flex-1 text-sm">
                <p className="font-semibold">{p.business_name}</p>
                <a href={p.website_url} target="_blank" rel="noopener noreferrer" className="block truncate text-brand-ink underline">
                  {p.website_url}
                </a>
                {p.linkedin_url && (
                  <a href={p.linkedin_url} target="_blank" rel="noopener noreferrer" className="block truncate text-brand-ink underline">
                    LinkedIn
                  </a>
                )}
                <ul className="mt-2 space-y-0.5">
                  {CHECK_LABELS.map(([k, label]) => (
                    <li key={k} className={p.checks[k] ? "text-good" : "text-bad"}>
                      {p.checks[k] ? "✓" : "✗"} {label}
                    </li>
                  ))}
                  <li className="text-ink-muted">Paid for {p.checks.paid_days} days</li>
                </ul>
              </div>
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <Button onClick={() => decide(p.id, true)}>Approve</Button>
              <input className="min-w-0 flex-1 rounded-xl border border-line bg-surface px-3 py-2 text-sm"
                placeholder="Reason for turning it down (the business sees this)" value={reasons[p.id] ?? ""}
                onChange={(e) => setReasons((r) => ({ ...r, [p.id]: e.target.value }))} />
              <Button variant="secondary" disabled={!reasons[p.id]?.trim()} onClick={() => decide(p.id, false)}>
                Turn down
              </Button>
            </div>
          </li>
        ))}
      </ul>

      <h4 className="mt-6 font-semibold">Add a company you work with</h4>
      <p className="mt-1 text-sm text-ink-muted">
        For a client you work with directly and invoice yourself — no account needed. It goes on the home page at
        once and stays until you remove it.
      </p>
      <div className="mt-3 grid gap-2 text-sm sm:grid-cols-2">
        <input className="rounded-xl border border-line bg-surface px-3 py-2" placeholder="Company name" aria-label="Company name"
          value={name} onChange={(e) => setName(e.target.value)} />
        <input className="rounded-xl border border-line bg-surface px-3 py-2" placeholder="Website, e.g. theircompany.com"
          aria-label="Website" value={website} onChange={(e) => setWebsite(e.target.value)} />
        <label className="grid gap-1 sm:col-span-2">
          <span>Logo (PNG, JPEG or WebP, up to 2 MB)</span>
          <input key={fileKey} type="file" accept="image/png,image/jpeg,image/webp" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        </label>
      </div>
      <Button className="mt-3" disabled={busy || !name.trim() || !website.trim() || !file} onClick={add}>
        {busy ? "Adding…" : "Add to the home page"}
      </Button>

      <h4 className="mt-6 font-semibold">On the home page now ({live.length})</h4>
      <p className="mt-1 text-sm text-ink-muted">
        Businesses that applied show only while their plan is paid; companies you added stay until you remove them.
      </p>
      <ul className="mt-3 space-y-2">
        {live.map((p) => (
          <li key={p.id} className="flex flex-wrap items-center gap-3 rounded-2xl border border-line p-3 text-sm">
            <Logo token={token} url={p.logo_url} name={p.business_name} width={p.logo_width} height={p.logo_height} />
            <div className="min-w-0 flex-1">
              <p className="font-semibold">{p.business_name}</p>
              <p className="truncate text-ink-muted">
                {p.website_url} · {p.source === "admin" ? "added by you" : "applied (paid plan)"}
              </p>
            </div>
            <Button variant="secondary" onClick={() => remove(p.id)}>
              Remove
            </Button>
          </li>
        ))}
      </ul>
      <ErrorBox message={error} />
    </Card>
  );
}
