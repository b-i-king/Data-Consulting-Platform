// "Add another source": open a second (third...) source NEXT TO the data that
// is already open — another file, stored data, a table from the client's
// database, or QuickBooks / Xero records. All of them arrive as the same kind
// of table (a CSV File), so the cleaning step takes them together: tables with
// the same columns are stacked into one, different ones sit side by side as
// tabs, and the categorizer says how they relate (a shared ID, or customers
// matched by name / email / phone — api/services/entity_matching.py).
//
// RULES (Workspace.tsx applies them to the combined data):
//   * the STRICTER rule wins: if any source is a cloud table or live records,
//     the cleaning page is a REPORT ONLY and "Load into database" is off;
//   * live sources are read again each time they open, which uses the tool's
//     daily allowance (Settings -> Integrations shows "calls today");
//   * NOTHING new is stored by combining: it all lives in this tab.

"use client";

import { useEffect, useState } from "react";
import { getCloudTableFile, getConnections, getLiveDatasetFile, getLiveDatasets, getStoredDataFile } from "@/lib/api";
import { fmtInt } from "@/lib/format";
import type { CloudTable, DataConnection, LiveDataset, WarehouseCounts } from "@/lib/types";
import { Button, ErrorBox } from "@/components/wizard/ui";

/** What a new source is, for the combined data's rules: a picked file, stored data, or read live. */
export type SourceKind = "file" | "stored" | "cloud";

export interface AddedSource {
  file: File;
  kind: SourceKind;
  /** Shown in the header: "sales.csv", "public.orders (database)", "Xero sales invoices". */
  label: string;
}

export default function AddSourceDialog(props: {
  token: string;
  stored: WarehouseCounts | null;
  cloudTables: CloudTable[] | null;
  /** The names already open (a source is offered only once). */
  openNames: string[];
  onAdd: (sources: AddedSource[]) => void;
  onClose: () => void;
}) {
  const [live, setLive] = useState<DataConnection[]>([]);
  const [datasets, setDatasets] = useState<Record<string, LiveDataset[]>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getConnections(props.token)
      .then((info) => setLive(info.connectors.filter((c) => c.live && c.status === "connected")))
      .catch(() => setLive([]));
  }, [props.token]);

  // Read one source (busy shows which), then hand it to the workspace.
  async function take(key: string, read: () => Promise<AddedSource>) {
    setBusy(key);
    setError(null);
    try {
      props.onAdd([await read()]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "We couldn't read that source.");
    } finally {
      setBusy(null);
    }
  }

  async function listRecords(connector: string) {
    setBusy(`list-${connector}`);
    setError(null);
    try {
      const r = await getLiveDatasets(props.token, connector);
      setDatasets((d) => ({ ...d, [connector]: r.datasets.filter((x) => x.has_rows) }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "We couldn't read the records.");
    } finally {
      setBusy(null);
    }
  }

  const isOpen = (name: string) => props.openNames.includes(name);
  const storedSources = (props.stored?.sources ?? []).filter((s) => !isOpen(s.source_name));
  const tables = (props.cloudTables ?? []).filter((t) => !isOpen(`${t.schema}.${t.name}.csv`));

  return (
    <div className="fixed inset-0 z-50 overflow-y-auto bg-black/40 p-4" role="dialog" aria-modal="true" aria-label="Add another source">
      <div className="mx-auto max-w-2xl space-y-5 rounded-2xl border border-line bg-canvas p-5 shadow-[var(--shadow-card)]">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-xl font-semibold">Add another source</h2>
            <p className="text-sm text-ink-muted">
              It opens next to what you have: matching tables are stacked, others sit side by side, and we show how they
              relate. Nothing new is stored.
            </p>
          </div>
          <Button variant="secondary" onClick={props.onClose}>
            Close
          </Button>
        </div>

        <section className="space-y-2">
          <h3 className="text-sm font-semibold">A file from your computer</h3>
          <input
            type="file"
            multiple
            aria-label="Choose files to add"
            onChange={(e) => {
              const picked = Array.from(e.target.files ?? []);
              if (picked.length) props.onAdd(picked.map((f) => ({ file: f, kind: "file", label: f.name })));
            }}
            className="block w-full text-sm"
          />
        </section>

        {storedSources.length > 0 && (
          <section className="space-y-2">
            <h3 className="text-sm font-semibold">Your stored data</h3>
            <div className="flex flex-wrap gap-2">
              {storedSources.map((s) => (
                <Button key={`${s.source_system}/${s.source_name}`} variant="secondary" disabled={busy !== null}
                  onClick={() => take(`stored-${s.source_name}`, async () => ({
                    file: await getStoredDataFile(props.token, s.source_name), kind: "stored", label: s.source_name,
                  }))}>
                  {busy === `stored-${s.source_name}` ? "Reading…" : `${s.source_name} · ${fmtInt(s.records)} rows`}
                </Button>
              ))}
            </div>
          </section>
        )}

        {tables.length > 0 && (
          <section className="space-y-2">
            <h3 className="text-sm font-semibold">From your database</h3>
            <div className="flex flex-wrap gap-2">
              {tables.slice(0, 24).map((t) => (
                <Button key={`${t.schema}.${t.name}`} variant="secondary" disabled={busy !== null}
                  onClick={() => take(`cloud-${t.schema}.${t.name}`, async () => ({
                    file: (await getCloudTableFile(props.token, t.schema, t.name)).file, kind: "cloud",
                    label: `${t.schema}.${t.name} (database)`,
                  }))}>
                  {busy === `cloud-${t.schema}.${t.name}` ? "Reading…" : `${t.schema}.${t.name}`}
                </Button>
              ))}
            </div>
          </section>
        )}

        {live.map((c) => (
          <section key={c.connector} className="space-y-2">
            <h3 className="text-sm font-semibold">
              {c.name} <span className="font-normal text-ink-muted">· read live, uses today&apos;s allowance</span>
            </h3>
            {!datasets[c.connector] ? (
              <Button variant="secondary" disabled={busy !== null} onClick={() => listRecords(c.connector)}>
                {busy === `list-${c.connector}` ? "Checking…" : "Show record types"}
              </Button>
            ) : datasets[c.connector].length === 0 ? (
              <p className="text-sm text-ink-muted">No records yet.</p>
            ) : (
              <div className="flex flex-wrap gap-2">
                {datasets[c.connector]
                  .filter((d) => !isOpen(`${c.connector}.${d.id}.csv`))
                  .map((d) => (
                    <Button key={d.id} variant="secondary" disabled={busy !== null}
                      onClick={() => take(`live-${c.connector}-${d.id}`, async () => ({
                        file: (await getLiveDatasetFile(props.token, c.connector, d.id)).file, kind: "cloud",
                        label: `${c.name} ${d.name.toLowerCase()}`,
                      }))}>
                      {busy === `live-${c.connector}-${d.id}` ? "Reading…" : d.name}
                    </Button>
                  ))}
              </div>
            )}
          </section>
        ))}

        <p className="text-xs text-ink-muted">
          Adding a database table or live records keeps the stricter rules for everything open: the cleaning page is a
          report (fix it at the source) and storing is off.
        </p>
        <ErrorBox message={error} />
      </div>
    </div>
  );
}
