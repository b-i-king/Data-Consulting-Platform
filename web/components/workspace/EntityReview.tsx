// "Is this the same customer?" — name matches between sources waiting for a person.
//
// When a stored source names a customer, vendor or product ALMOST like one
// already stored ("Corner Market ATL" / "Corner Market", a score of 80-91),
// the two are kept apart until someone decides (api/services/entities.py).
// "Same" merges them: every source then counts as one customer in dashboards
// and analysis. "Different" keeps them apart. No stored row changes either way.
// Shown in Settings -> Integrations only when there is something to decide.

"use client";

import { useEffect, useState } from "react";
import { decideEntity, getEntityReview } from "@/lib/api";
import type { EntityMatch } from "@/lib/types";
import { Button, Card, ErrorBox } from "@/components/wizard/ui";

const KIND: Record<EntityMatch["entity_type"], string> = {
  customer: "customer", vendor: "supplier", item: "product", payee: "payee", location: "location",
};
const SOURCE: Record<string, string> = { file_upload: "a file", quickbooks: "QuickBooks", xero: "Xero" };

export default function EntityReview(props: { token: string; canChange: boolean }) {
  const [matches, setMatches] = useState<EntityMatch[]>([]);
  const [busy, setBusy] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getEntityReview(props.token)
      .then((r) => setMatches(r.matches))
      .catch(() => setMatches([]));
  }, [props.token]);

  async function answer(m: EntityMatch, same: boolean) {
    setBusy(m.link_id);
    setError(null);
    try {
      setMatches((await decideEntity(props.token, m.link_id, same)).matches);
    } catch (e) {
      setError(e instanceof Error ? e.message : "We couldn't save that.");
    } finally {
      setBusy(null);
    }
  }

  if (matches.length === 0) return null;
  return (
    <Card className="space-y-3 border-warn/40">
      <div>
        <h2 className="font-semibold">Is this the same {matches.length === 1 ? KIND[matches[0].entity_type] : "one"}?</h2>
        <p className="text-sm text-ink-muted">
          These names look alike across your sources. Say &quot;same&quot; and they count as one in every dashboard.
        </p>
      </div>
      <ul className="divide-y divide-line rounded-xl border border-line">
        {matches.map((m) => (
          <li key={m.link_id} className="flex flex-col gap-2 px-3 py-2 text-sm sm:flex-row sm:items-center sm:justify-between">
            <span>
              <strong>{m.name}</strong> <span className="text-ink-muted">({SOURCE[m.source_system] ?? m.source_system})</span>
              {" ≈ "}
              <strong>{m.candidate.name}</strong>{" "}
              <span className="text-ink-muted">
                ({m.candidate.sources.map((s) => SOURCE[s] ?? s).join(", ")}) · a {KIND[m.entity_type]} · {m.score}% alike
              </span>
            </span>
            {props.canChange && (
              <span className="flex gap-2">
                <Button disabled={busy !== null} onClick={() => answer(m, true)}>Same</Button>
                <Button variant="secondary" disabled={busy !== null} onClick={() => answer(m, false)}>Different</Button>
              </span>
            )}
          </li>
        ))}
      </ul>
      <ErrorBox message={error} />
    </Card>
  );
}
