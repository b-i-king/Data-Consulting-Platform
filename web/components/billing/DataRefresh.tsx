// "Connected data" in Plan & billing: how often each connector (QuickBooks
// first) pulls new data into managed storage.
//
//   QuickBooks Online · coming soon
//   ( ) Only when I ask   ( ) Once a week   (•) Once a day   ( ) Every hour +$15   ( ) Live +$40
//
// Rules (the server checks them: api/services/connections.py):
//   * connectors need the Data Engineering tool (they store data for the client)
//   * daily is the default; manual / weekly / daily are included, hourly and
//     live are paid add-ons (free until connectors launch)
//   * "Refresh now" can't run more often than the chosen frequency allows
// Prices come from the server (api/services/pricing.py REFRESH_OPTIONS).

"use client";

import { useEffect, useState } from "react";
import { getConnections, setRefresh } from "@/lib/api";
import type { ConnectionsInfo, RefreshFrequency } from "@/lib/types";
import { Card, ErrorBox } from "@/components/wizard/ui";

export default function DataRefresh(props: { token: string; canChange: boolean }) {
  const [info, setInfo] = useState<ConnectionsInfo | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Load the choices and the client's connectors once (and after a change of client).
  useEffect(() => {
    getConnections(props.token)
      .then(setInfo)
      .catch((e) => setError(e instanceof Error ? e.message : "Couldn't load connected data."));
  }, [props.token]);

  async function choose(connector: string, frequency: RefreshFrequency) {
    if (!info) return;
    setBusy(connector);
    setError(null);
    try {
      const res = await setRefresh(props.token, connector, frequency);
      setInfo({ ...info, connectors: res.connectors });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't save the refresh choice.");
    } finally {
      setBusy(null);
    }
  }

  if (!info) return error ? <ErrorBox message={error} /> : null;
  const locked = !info.can_connect || !props.canChange;

  return (
    <Card>
      <h2 className="font-semibold">Connected data</h2>
      <p className="mt-1 text-sm text-ink-muted">
        Choose how often each connected source pulls new and changed records. Once a day is the default; faster
        updates are paid add-ons.
        {!info.can_connect && " Connected sources need the Data Engineering tool in your package."}
      </p>
      {info.connectors.map((c) => (
        <fieldset key={c.connector} className="mt-3" disabled={locked || busy === c.connector}>
          <legend className="text-sm font-semibold">
            {c.name}
            {!c.available && <span className="ml-2 text-xs font-normal text-ink-muted">coming soon</span>}
          </legend>
          <div role="radiogroup" aria-label={`${c.name} refresh`} className="mt-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {info.options.map((o) => {
              const on = c.refresh_frequency === o.id;
              return (
                <label
                  key={o.id}
                  className={`flex cursor-pointer flex-col rounded-xl border p-3 text-sm ${
                    on ? "border-brand bg-brand-soft" : "border-line bg-surface hover:border-brand"
                  } ${locked ? "cursor-not-allowed opacity-60" : ""}`}
                >
                  <span className="flex items-center justify-between gap-2">
                    <span className="flex items-center gap-2 font-semibold">
                      <input
                        type="radio"
                        name={`refresh-${c.connector}`}
                        checked={on}
                        onChange={() => choose(c.connector, o.id)}
                      />
                      {o.label}
                    </span>
                    <span className="font-mono text-xs tabular-nums">{o.price_label}</span>
                  </span>
                  <span className="mt-1 text-xs text-ink-muted">{o.description}</span>
                  {!o.included && !o.billable && (
                    <span className="mt-1 text-xs text-good">Free until connectors launch</span>
                  )}
                </label>
              );
            })}
          </div>
        </fieldset>
      ))}
      <ErrorBox message={error} />
    </Card>
  );
}
