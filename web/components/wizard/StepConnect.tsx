// Get Started — "Connect" (the cloud storage and business app paths).
//
// The same cards as Settings -> Integrations (components/workspace/
// IntegrationsPanel.tsx), so the rules are identical to the workspace:
//   * Supabase / PostgreSQL: "Sign in with Supabase" or paste a connection
//     string; then the tables schema by schema (empty ones hidden). Picking
//     one ("Preview this table") reads it, read-only, for the next step.
//   * QuickBooks / Xero: "Connect" opens the tool's own sign-in page. The
//     browser LEAVES Get Started and comes back to /get-started/connect?integration=...;
//     Wizard.tsx saved its place first (sessionStorage) and returns here.
//     When every connection spot is taken (Xero 5, QuickBooks 15), the card
//     says so and the file-export path is offered instead.
// The account was created on the step before: connections are saved,
// encrypted, on it.

"use client";

import { useEffect, useState } from "react";
import { getConnections } from "@/lib/api";
import type { CloudTable, ConnectionsInfo, DataConnection } from "@/lib/types";
import { BusinessToolCard, SupabaseCard } from "@/components/workspace/IntegrationsPanel";
import { Button, ErrorBox, NavRow, StepHeader } from "./ui";
import type { DataSource } from "./StepDataLocation";

export default function StepConnect(props: {
  token: string;
  connector: string; // "supabase" | "quickbooks" | "xero"
  source: DataSource;
  /** After coming back from the provider's sign-in: what happened. */
  notice: { ok: boolean; text: string } | null;
  /** Cloud: a table was picked to preview. */
  onPickTable: (t: CloudTable) => void;
  /** Reading the picked table (cloud). */
  busy: boolean;
  error: string | null;
  /** Switch to the files path (export a file from the tool instead). */
  onFileExport: () => void;
  onBack: () => void;
  /** Business apps: go on to the quick look (once connected). */
  onNext: () => void;
}) {
  const [info, setInfo] = useState<ConnectionsInfo | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    getConnections(props.token)
      .then(setInfo)
      .catch((e) => setLoadError(e instanceof Error ? e.message : "We couldn't load your connections."));
  }, [props.token]);

  const c: DataConnection | undefined = info?.connectors.find((x) => x.connector === props.connector);
  const changed = (connectors: DataConnection[]) => info && setInfo({ ...info, connectors });
  const slots = info?.slots?.[props.connector];
  const full = !!slots && slots.used >= slots.max && c?.status !== "connected";
  const connected = c?.status === "connected";

  return (
    <section className="space-y-4">
      <StepHeader
        title={`Connect ${c?.name ?? "your data"}`}
        subtitle={
          props.source === "business"
            ? "Sign in on its own page and allow read-only access. Nothing is copied — we read it when you ask."
            : "Read-only, where it lives. Then pick a table to preview."
        }
      />
      {props.notice && (
        <p role="status"
          className={`rounded-2xl border p-3 text-sm ${props.notice.ok ? "border-good/40 bg-good/10" : "border-bad/40 bg-bad/10"}`}>
          {props.notice.text}
        </p>
      )}
      {!info && !loadError && <p className="text-sm text-ink-muted">Loading…</p>}
      <ErrorBox message={loadError} />

      {c && props.source === "cloud" && (
        <SupabaseCard token={props.token} connector={c} canChange onChanged={changed} onOpenTable={props.onPickTable}
          openLabel={props.busy ? "Reading…" : "Preview this table"} returnTo="start" />
      )}
      {c && props.source === "business" && (
        <BusinessToolCard token={props.token} connector={c} canChange onChanged={changed} onOpenLive={() => {}}
          full={full} connectOnly returnTo="start" />
      )}
      <ErrorBox message={props.error} />

      {(full || (c && !c.available)) && (
        <Button variant="secondary" onClick={props.onFileExport}>
          Continue with a file export instead
        </Button>
      )}

      <NavRow onBack={props.onBack}>
        {props.source === "business" ? (
          <Button onClick={props.onNext} disabled={!connected}>
            Continue to the quick look
          </Button>
        ) : (
          <span className="text-sm text-ink-muted">{connected ? "Pick a table above to preview it." : ""}</span>
        )}
      </NavRow>
    </section>
  );
}
