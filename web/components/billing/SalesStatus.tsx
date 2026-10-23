"use client";

// ── Purchases open or closed (the beta switch) ────────────────────────────────
// While an admin keeps purchases closed (api/services/sales.py), customers see
// a friendly note in place of the checkout buttons, and the buttons are
// disabled; the server refuses a checkout anyway. Admins and testers can still
// buy, to try the flow end to end. `useSalesStatus` asks the API once per page.

import { useEffect, useState } from "react";
import { getSalesStatus } from "@/lib/api";

export function useSalesStatus(token?: string) {
  // Until the answer arrives, act as open: the server still refuses if it isn't.
  const [state, setState] = useState({ open: true, canBuy: true, message: "" });
  useEffect(() => {
    getSalesStatus(token)
      .then((s) => setState({ open: s.open, canBuy: s.can_buy, message: s.message }))
      .catch(() => {});
  }, [token]);
  return state;
}

export function SalesClosedNote({ message }: { message: string }) {
  return (
    <p role="status" className="rounded-2xl border border-brand/40 bg-brand-soft p-3 text-sm">
      <strong>Beta: </strong>
      {message}
    </p>
  );
}
