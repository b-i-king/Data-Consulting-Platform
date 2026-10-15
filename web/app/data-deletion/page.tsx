// "/data-deletion" — how to delete your data. Adapted from the
// Video_Game_Stats_Tracker template (web/app/data-deletion). Two paths:
//   1. Stored data (managed storage): self-service, deleted right away
//      (POST /api/warehouse/delete, the "Delete stored data" button).
//   2. The whole account: by email, confirmed within 5 business days and
//      deleted within 30 days (done by hand for now).

import type { Metadata } from "next";
import { Email, LegalPage, List, Section, Strong } from "@/components/legal/LegalPage";

export const metadata: Metadata = { title: "Data Deletion" };

export default function DataDeletionPage() {
  return (
    <LegalPage title="Data Deletion" updated="10/15/2026">
      <Section title="Files You Upload">
        <p>
          Nothing to delete: uploaded files are processed in memory and never kept, unless you chose managed storage.
        </p>
      </Section>

      <Section title="Delete Your Stored Data (Right Away)">
        <p>If you use managed storage, you can delete everything stored for your account yourself:</p>
        <List ordered>
          <li>Sign in and open your workspace.</li>
          <li>
            Go to <Strong>Data Engineering</Strong>, open your stored data (or any file), and find the{" "}
            <Strong>Stored for …</Strong> card.
          </li>
          <li>
            Optional: <Strong>Download as CSV</Strong> to keep a copy.
          </li>
          <li>
            Press <Strong>Delete stored data</Strong> and confirm. Every stored row, customer and product is deleted
            immediately, and your storage consent is withdrawn.
          </li>
        </List>
        <p className="text-ink-muted">
          For a business account, the account owner (or your consultant) can do this. Your account stays open.
        </p>
      </Section>

      <Section title="Close Your Account">
        <List ordered>
          <li>
            Email <Email subject="Data Deletion Request" /> with the subject line{" "}
            <em>&ldquo;Data Deletion Request&rdquo;</em> from the email address on your account.
          </li>
          <li>
            We will <Strong>confirm within 5 business days</Strong> and delete your account and all data linked to it
            within <Strong>30 days</Strong>.
          </li>
          <li>You will receive a final confirmation email once deletion is complete.</li>
        </List>
      </Section>

      <Section title="What Gets Deleted">
        <List>
          <li>Your account: name, email, business details, location, and sign-in.</li>
          <li>Your team members&apos; accounts (for a business owner closing the business account).</li>
          <li>Everything in managed storage, and its load history.</li>
          <li>Your consultation records and download history.</li>
        </List>
      </Section>

      <Section title="What Is Not Deleted">
        <List>
          <li>
            <Strong>Billing and tax records</Strong> we are required by law to keep.
          </li>
          <li>
            <Strong>Records held by other services</Strong> — Stripe (payments) and Cal.com (bookings) keep their own
            records under their own policies.
          </li>
        </List>
      </Section>

      <Section title="Contact">
        <p>
          <Email subject="Data Deletion Request" />
        </p>
      </Section>
    </LegalPage>
  );
}
