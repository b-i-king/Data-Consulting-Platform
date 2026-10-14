// "Confirm your download" — shown before any download starts.
//
// Downloading re-runs the cleaning on the server, and that processing time is
// billed by the minute. So before the file is made, this box shows:
//   * which table, how many rows × fields, and the file type
//   * about how long it will take (estimated from rows × fields by
//     api/services/pricing.py download_estimate; Excel is slower than CSV)
//   * the total charge
// The confirmed total is sent with the download, and the server never charges
// more than it — even if this run takes a little longer than the estimate.
// With a plan that includes Data Cleaning (`included`), the total is $0.00.

"use client";

import { fmtInt, fmtUsd } from "@/lib/format";
import type { DownloadEstimate } from "@/lib/types";
import { ConfirmDialog, TotalRow } from "./ui";

export type DownloadFormat = "csv" | "xlsx";

const FORMAT_NAME: Record<DownloadFormat, string> = { csv: "CSV", xlsx: "Excel" };

/** Short "≈ under 10 seconds · $0.50" line for a download button's card. */
export function estimateLine(est: DownloadEstimate, format: DownloadFormat = "csv"): string {
  return `≈ ${est.formats[format].label} · ${fmtUsd(est.amount_usd)}`;
}

export default function DownloadConfirm(props: {
  tableLabel: string;
  format: DownloadFormat;
  estimate: DownloadEstimate;
  /** The account's plan includes Data Cleaning: no per-minute charge. */
  included?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const { estimate: est, format } = props;
  const wait = est.formats[format];
  const total = props.included ? 0 : est.amount_usd;
  return (
    <ConfirmDialog
      title="Confirm your download"
      confirmLabel={`Download ${FORMAT_NAME[format]} · ${fmtUsd(total)}`}
      onConfirm={props.onConfirm}
      onCancel={props.onCancel}
    >
      <p>
        <strong>{props.tableLabel}</strong> — {fmtInt(est.rows)} rows × {fmtInt(est.columns)} fields (
        {fmtInt(est.cells)} cells) as {FORMAT_NAME[format]}.
      </p>
      <div className="space-y-1 rounded-xl bg-surface-2 p-3">
        <TotalRow label="Estimated time" value={wait.label} />
        <TotalRow
          label={
            props.included
              ? "Processing — included in your plan"
              : `Processing (${est.billable_minutes} billable minute${est.billable_minutes === 1 ? "" : "s"})`
          }
          value={fmtUsd(total)}
        />
        <TotalRow label="Total you'll pay" value={fmtUsd(total)} strong />
      </div>
      {!wait.under_five_minutes && (
        <p className="text-warn">
          This is a large table, so it may take more than five minutes. Keep this page open until the file arrives.
          {format === "xlsx" && " CSV is much faster for big tables."}
        </p>
      )}
      <p className="text-xs text-ink-muted">
        Billed by processing time, rounded up to the minute. You&apos;ll never be charged more than this total, even if
        it takes a little longer.
      </p>
    </ConfirmDialog>
  );
}
