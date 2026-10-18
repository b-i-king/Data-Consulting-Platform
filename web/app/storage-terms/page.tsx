// "/storage-terms" — Managed Storage Terms: what a client agrees to when they
// tick "Store this data" before a Data Engineering load
// (components/explore/WarehouseLoad.tsx). The version date here MUST match
// TERMS_VERSION in api/routers/warehouse.py — it is saved with each consent
// (table storage_consents), so change both together.

import type { Metadata } from "next";
import Link from "next/link";
import { COMPANY, Email, LegalPage, List, Section, Strong } from "@/components/legal/LegalPage";

export const metadata: Metadata = { title: "Managed Storage Terms" };

// Same value as TERMS_VERSION in api/routers/warehouse.py (ISO format there).
const VERSION = "2026-10-15";

export default function StorageTermsPage() {
  return (
    <LegalPage title="Managed Storage Terms" updated="10/15/2026">
      <p className="text-sm text-ink-muted">Version {VERSION}</p>

      <Section title="What Managed Storage Is">
        <p>
          Normally, files you upload are processed in memory and discarded. With managed storage (part of Data
          Engineering), the cleaned data you approve is kept for you so you — and your consultant, if you have one — can
          open the same dashboards and analysis anytime without uploading again.
        </p>
      </Section>

      <Section title="Nothing Is Stored Without Your OK">
        <List>
          <li>
            Before anything is stored you see a <Strong>preview</Strong>: every change made so your data fits the
            database, why it&apos;s needed, and the first rows exactly as they will be stored.
          </li>
          <li>
            Data is stored only when you (or your consultant, for you) press <Strong>Accept and load</Strong> and agree
            to these terms. Exactly what you previewed is stored — if the data or choices change, you are asked again.
          </li>
        </List>
      </Section>

      <Section title="What Is Stored">
        <List>
          <li>The rows you accepted (for example sales, expenses or feedback), with their dates and amounts.</li>
          <li>Your customers and products, once each, linked to those rows.</li>
          <li>Any other columns from your file, kept as they are.</li>
          <li>A record of each load: when, which file, how many rows, and who started it.</li>
        </List>
      </Section>

      <Section title="Who Can See It">
        <List>
          <li>You and the people on your team.</li>
          <li>Your assigned consultant.</li>
          <li>
            No other client. Every row is tagged with your account, and the database itself blocks other accounts from
            reading it.
          </li>
        </List>
      </Section>

      <Section title="How We Use It">
        <p>
          Only to provide the services you choose. We do not sell it, use it for advertising, or share it with anyone
          other than the providers that run the Platform (see the{" "}
          <Link href="/privacy" className="text-brand-ink hover:underline">
            Privacy Policy
          </Link>
          ).
        </p>
      </Section>

      <Section title="Where It Is Kept">
        <p>In our database at Supabase (PostgreSQL, hosted on Amazon Web Services in the United States).</p>
      </Section>

      <Section title="Your Control">
        <List>
          <li>
            <Strong>Download</Strong> everything stored as a CSV file anytime.
          </li>
          <li>
            <Strong>Delete</Strong> it anytime: Data Engineering → Stored for … → Delete stored data. It is deleted right
            away and your consent is withdrawn. See{" "}
            <Link href="/data-deletion" className="text-brand-ink hover:underline">
              Data Deletion
            </Link>
            .
          </li>
          <li>Closing your account deletes your stored data too.</li>
        </List>
      </Section>

      <Section title="Contact">
        <p>
          {COMPANY} — <Email subject="Managed storage question" />
        </p>
      </Section>
    </LegalPage>
  );
}
