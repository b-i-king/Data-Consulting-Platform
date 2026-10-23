"use client";

// ── Admin: open or close purchases ────────────────────────────────────────────
// The beta switch (api/services/sales.py): while closed, customers can't buy
// plans or consultant help (admins and testers still can). Flip it here when
// the hosting is upgraded — it takes effect at once, no redeploy.

import { useState } from "react";
import { setSalesOpen } from "@/lib/api";
import { useSalesStatus } from "@/components/billing/SalesStatus";
import { Button, Card, ConfirmDialog, ErrorBox } from "@/components/wizard/ui";

export default function AdminSalesSwitch({ token }: { token: string }) {
  const status = useSalesStatus(token);
  const [open, setOpen] = useState<boolean | null>(null);   // after a change; null = what the server said
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const isOpen = open ?? status.open;

  async function flip() {
    setAsking(false);
    setError(null);
    try {
      setOpen((await setSalesOpen(token, !isOpen)).open);
    } catch (e) {
      setError(e instanceof Error ? e.message : "That didn't go through.");
    }
  }

  return (
    <Card>
      <h3 className="font-semibold">Purchases</h3>
      <p className="mt-1 text-sm">
        Customers can buy plans and consultant help: <strong>{isOpen ? "Open" : "Closed (beta)"}</strong>
      </p>
      <p className="mt-1 text-sm text-ink-muted">
        While closed, customers see a &ldquo;plans open soon&rdquo; note instead of checkout. You and testers can still
        check out to try the flow. Open it once Render and Vercel are on paid plans.
      </p>
      <Button className="mt-3" variant={isOpen ? "secondary" : "primary"} onClick={() => setAsking(true)}>
        {isOpen ? "Close purchases" : "Open purchases"}
      </Button>
      {asking && (
        <ConfirmDialog
          title={isOpen ? "Close purchases?" : "Open purchases to customers?"}
          confirmLabel={isOpen ? "Close purchases" : "Open purchases"}
          onCancel={() => setAsking(false)}
          onConfirm={flip}
        >
          <p className="text-sm">
            {isOpen
              ? "Customers won't be able to start new purchases. Existing subscriptions keep running."
              : "Customers will be able to buy plans and consultant help right away. Check that Stripe is set to live keys first."}
          </p>
        </ConfirmDialog>
      )}
      <ErrorBox message={error} />
    </Card>
  );
}
