// "/privacy" — Privacy Policy. Adapted from the Video_Game_Stats_Tracker
// template (web/app/privacy) for this platform: what we collect, what we never
// store unless asked (client data), who we share with, and the client's rights.
// Keep it in step with docs/ARCHITECTURE.md and db/schema.sql when what we
// store changes.

import type { Metadata } from "next";
import Link from "next/link";
import { COMPANY, Email, LegalPage, List, Section, Strong } from "@/components/legal/LegalPage";
import { SITE_NAME } from "@/lib/format";

export const metadata: Metadata = { title: "Privacy Policy" };

export default function PrivacyPage() {
  return (
    <LegalPage title="Privacy Policy" updated="10/15/2026">
      <Section title="Introduction">
        <p>
          Welcome to <strong>{SITE_NAME}</strong> (&quot;the Platform&quot;), operated by <strong>{COMPANY}</strong>. This
          Privacy Policy explains what information we collect when you use the Platform, how we use it, and the choices
          you have.
        </p>
      </Section>

      <Section title="Information We Collect">
        <List>
          <li>
            <Strong>Account information</Strong> — your name, email address, account type (individual or business),
            and, if you give them, your business name, industry, company size, city, and team members&apos; names and
            emails.
          </li>
          <li>
            <Strong>Your password</Strong> — handled by our sign-in provider, Supabase Auth. We never store your
            password itself.
          </li>
          <li>
            <Strong>Files you upload</Strong> — processed in memory to clean and analyze them, then discarded. We do{" "}
            <em>not</em> keep them, unless you choose managed storage (below).
          </li>
          <li>
            <Strong>Managed storage (only if you opt in)</Strong> — if you (or your consultant, for you) accept a
            preview and agree to the{" "}
            <Link href="/storage-terms" className="text-brand hover:underline">
              Storage Terms
            </Link>
            , the cleaned data you approved is stored so you can view it later.
          </li>
          <li>
            <Strong>Usage records</Strong> — for each download: the table&apos;s name, row count, processing time and
            amount charged. Never the data itself.
          </li>
          <li>
            <Strong>Consultations</Strong> — when you book a consultation, our scheduling provider (Cal.com) tells us the
            booking time and your name and email, so we know whether your free consultation has been used.
          </li>
          <li>
            <Strong>Technical logs</Strong> — basic server logs (IP address, request times) for security and
            debugging.
          </li>
        </List>
      </Section>

      <Section title="How We Use Your Information">
        <List>
          <li>To create your account, sign you in, and give you the services on your plan.</li>
          <li>To let your consultant work on your account, if you asked for one.</li>
          <li>To bill you for what you use and manage your subscription.</li>
          <li>To schedule and keep track of consultations.</li>
          <li>To keep the Platform secure and improve it. We do not use your data for advertising.</li>
        </List>
      </Section>

      <Section title="Who Can See Your Data">
        <List>
          <li>
            <Strong>You and your team</Strong> — everyone on a business account shares its plan and stored data.
          </li>
          <li>
            <Strong>Your consultant</Strong> — only if you asked for one or they created your account for you. A
            consultant can only open the clients assigned to them.
          </li>
          <li>No other client can ever see your data.</li>
        </List>
      </Section>

      <Section title="Data Sharing">
        <p>We do not sell or rent your personal data. We share it only with the services that run the Platform:</p>
        <List>
          <li>
            <Strong>Supabase</Strong> — database and sign-in (hosted on Amazon Web Services).
          </li>
          <li>
            <Strong>Render</Strong> — runs our server, where your files are processed.
          </li>
          <li>
            <Strong>Vercel</Strong> — hosts the website.
          </li>
          <li>
            <Strong>Cal.com</Strong> — consultation scheduling and, for paid sessions, payment under Cal.com&apos;s own
            terms.
          </li>
          <li>
            <Strong>Stripe</Strong> — subscription payments. Stripe handles all card data; we never store card details.
          </li>
        </List>
      </Section>

      <Section title="Data Retention">
        <p>
          Account information is kept while your account is open. Uploaded files are not kept. Data in managed storage
          is kept until you delete it or close your account. Billing records may be kept longer where the law requires.
        </p>
      </Section>

      <Section title="Your Choices">
        <List>
          <li>
            <Strong>Download</Strong> your stored data as a CSV file at any time (Data Engineering → Stored for … →
            Download as CSV).
          </li>
          <li>
            <Strong>Delete</Strong> your stored data at any time, or ask us to close your account — see{" "}
            <Link href="/data-deletion" className="text-brand hover:underline">
              Data Deletion
            </Link>
            .
          </li>
          <li>Correct your account details by emailing us.</li>
        </List>
      </Section>

      <Section title="Changes to This Policy">
        <p>We may update this policy. The date at the top shows the latest version.</p>
      </Section>

      <Section title="Contact">
        <p>
          Questions? Email us at <Email subject="Privacy question" />.
        </p>
      </Section>
    </LegalPage>
  );
}
