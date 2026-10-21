"use client";

// ── "Trusted by" listing (Settings -> Account, business accounts) ────────────
// A business can ask to have its logo in the home page's "Trusted by"
// row ("Trusted by"), linked to its website (api/services/partners.py). The card shows
// the rules FIRST, then:
//   * not eligible yet -> what's missing (e.g. "You can apply in 12 more days");
//   * eligible         -> the form: website, LinkedIn company page (optional),
//                         logo file, and "I've read the requirements";
//   * applied          -> the status (waiting for review / live / turned down
//                         with the reason), the logo as it will look, and
//                         "Send a new logo" or "Take my logo down".
// The logo is shrunk and converted on the server (a small WebP), so any PNG,
// JPEG or WebP up to 2 MB is fine.

import { useEffect, useState } from "react";
import { applyAsPartner, getMyPartner, getPrivateLogo, withdrawPartner } from "@/lib/api";
import type { PartnerStatus } from "@/lib/types";
import { Button, Card, ErrorBox } from "@/components/wizard/ui";
import ThemedLogo from "@/components/home/ThemedLogo";

const STATUS_TEXT: Record<string, string> = {
  pending: "Waiting for review — a person checks every logo, usually within a few business days.",
  approved: "Live on the home page while your plan is paid.",
  rejected: "Not approved this time.",
  withdrawn: "Taken down. You can send it again any time.",
};
const input = "w-full rounded-xl border border-line bg-surface px-3 py-2 text-sm";

export default function PartnerCard({ token }: { token: string }) {
  const [info, setInfo] = useState<PartnerStatus | null>(null);
  const [logoPreview, setLogoPreview] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [website, setWebsite] = useState("");
  const [linkedin, setLinkedin] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [agreed, setAgreed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getMyPartner(token).then(setInfo).catch((e) => setError(e instanceof Error ? e.message : "Couldn't load your listing."));
  }, [token]);

  // Their own logo isn't public until approved, so it's fetched with their sign-in.
  const logoUrl = info?.application?.logo_url;
  useEffect(() => {
    if (!logoUrl) return;
    let url: string | null = null;
    getPrivateLogo(token, logoUrl).then((u) => setLogoPreview((url = u))).catch(() => setLogoPreview(null));
    return () => {
      if (url) URL.revokeObjectURL(url);
    };
  }, [token, logoUrl]);

  async function submit() {
    if (!file) return setError("Please choose your logo file.");
    setBusy(true);
    setError(null);
    try {
      setInfo(await applyAsPartner(token, { website, linkedin, logo: file, agreed }));
      setEditing(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "We couldn't send that.");
    } finally {
      setBusy(false);
    }
  }

  async function takeDown() {
    setBusy(true);
    setError(null);
    try {
      setInfo(await withdrawPartner(token));
    } catch (e) {
      setError(e instanceof Error ? e.message : "We couldn't take it down.");
    } finally {
      setBusy(false);
    }
  }

  const app = info?.application;
  const showForm = info?.eligible && (!app || editing);

  return (
    <Card>
      <h3 className="font-semibold">&ldquo;Trusted by&rdquo; listing</h3>
      <p className="mt-1 text-sm text-ink-muted">
        Show your logo in the home page&apos;s &ldquo;Trusted by&rdquo; row, linked to your website.
      </p>
      {info && (
        <details className="mt-3 text-sm">
          <summary className="cursor-pointer font-medium">The requirements</summary>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-ink-muted">
            {info.requirements.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        </details>
      )}

      {info && !info.eligible && !app && (
        <ul className="mt-3 space-y-1 text-sm">
          {info.missing.map((m) => (
            <li key={m}>• {m}</li>
          ))}
        </ul>
      )}

      {app && !editing && (
        <div className="mt-4 flex flex-wrap items-center gap-4">
          {logoPreview && (
            // How it will look on the home page: their logo's shape in the site's colors (gold on hover).
            <div className="group flex h-20 w-44 items-center justify-center rounded-2xl border border-line bg-surface px-4"
              title="How your logo will look on the home page">
              <ThemedLogo src={logoPreview} name={`${app.business_name} logo`} width={app.logo_width} height={app.logo_height} />
            </div>
          )}
          <div className="min-w-0 flex-1 text-sm">
            <p className="font-semibold">{STATUS_TEXT[app.status]}</p>
            {app.status === "rejected" && app.reason && <p className="mt-1">Reason: {app.reason}</p>}
            <p className="mt-1 truncate text-ink-muted">{app.website_url}</p>
          </div>
          <div className="flex flex-wrap gap-2">
            {info?.eligible && (
              <Button variant="secondary" onClick={() => { setWebsite(app.website_url); setLinkedin(app.linkedin_url ?? ""); setEditing(true); }}>
                Send a new logo
              </Button>
            )}
            {(app.status === "approved" || app.status === "pending") && (
              <Button variant="secondary" disabled={busy} onClick={takeDown}>
                Take my logo down
              </Button>
            )}
          </div>
        </div>
      )}

      {showForm && (
        <div className="mt-4 grid gap-3 text-sm">
          <label className="grid gap-1">
            <span className="font-medium">Your website</span>
            <input className={input} value={website} onChange={(e) => setWebsite(e.target.value)} placeholder="yourbusiness.com" />
          </label>
          <label className="grid gap-1">
            <span className="font-medium">LinkedIn company page (optional)</span>
            <input className={input} value={linkedin} onChange={(e) => setLinkedin(e.target.value)}
              placeholder="https://www.linkedin.com/company/your-business" />
          </label>
          <label className="grid gap-1">
            <span className="font-medium">
              Logo: just the logo on a transparent background (PNG or WebP, up to 2 MB). A plain one-color
              background is removed for you. It&apos;s shown in the site&apos;s colors, so its shape is what counts.
            </span>
            <input type="file" accept="image/png,image/jpeg,image/webp" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          </label>
          <label className="flex items-start gap-2">
            <input type="checkbox" className="mt-1 accent-[var(--color-brand)]" checked={agreed} onChange={(e) => setAgreed(e.target.checked)} />
            <span>
              I&apos;ve read the requirements: the logo is clean and family-friendly, it&apos;s shown only while our plan
              is paid, and it comes down automatically if we cancel.
            </span>
          </label>
          <div className="flex flex-wrap gap-2">
            <Button disabled={busy || !agreed || !website || !file} onClick={submit}>
              {busy ? "Sending…" : "Send for review"}
            </Button>
            {editing && (
              <Button variant="secondary" onClick={() => setEditing(false)}>
                Cancel
              </Button>
            )}
          </div>
        </div>
      )}
      <ErrorBox message={error} />
    </Card>
  );
}
