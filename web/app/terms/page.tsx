// "/terms" — Terms of Service. Adapted from the Video_Game_Stats_Tracker
// template (web/app/terms) for a data consulting platform: accounts,
// subscriptions, consultations, who owns the data, and limits of liability.

import type { Metadata } from "next";
import Link from "next/link";
import { COMPANY, Email, LegalPage, List, Section } from "@/components/legal/LegalPage";
import { SITE_NAME } from "@/lib/format";

export const metadata: Metadata = { title: "Terms of Service" };

export default function TermsPage() {
  return (
    <LegalPage title="Terms of Service" updated="10/15/2026">
      <Section title="Acceptance of Terms">
        <p>
          By using <strong>{SITE_NAME}</strong> (&quot;the Platform&quot;), operated by <strong>{COMPANY}</strong>, you
          agree to these Terms of Service and our{" "}
          <Link href="/privacy" className="text-brand hover:underline">
            Privacy Policy
          </Link>
          . If you do not agree, please do not use the Platform.
        </p>
      </Section>

      <Section title="Your Account">
        <List>
          <li>Give accurate information and keep your password private. You are responsible for activity on your account.</li>
          <li>
            A business account&apos;s owner manages its plan and may add team members, who share the account&apos;s
            data.
          </li>
          <li>
            If you ask for a consultant, you allow them to sign in to your workspace and work on your data for you.
          </li>
        </List>
      </Section>

      <Section title="Your Data">
        <List>
          <li>
            <strong>You own your data.</strong> You give us permission to process it only to provide the services you
            choose.
          </li>
          <li>Only upload data you have the right to use, and do not upload anything unlawful.</li>
          <li>
            Uploaded files are not kept. If you choose managed storage, the{" "}
            <Link href="/storage-terms" className="text-brand hover:underline">
              Storage Terms
            </Link>{" "}
            also apply.
          </li>
        </List>
      </Section>

      <Section title="Subscriptions and Payments">
        <List>
          <li>
            Subscriptions renew automatically each month or year until canceled. You can cancel anytime and keep access
            until the end of the period you paid for.
          </li>
          <li>Data cleaning downloads without a package are charged by processing time, shown before you confirm.</li>
          <li>
            Your first 30-minute consultation is free (Data Strategy &amp; Discovery is paid from the start). Later
            sessions are booked and paid through our scheduling page.
          </li>
        </List>
      </Section>

      <Section title="Results and Advice">
        <p>
          Analyses, dashboards, forecasts and answers are based on the data you provide and are for information. They
          are not legal, tax, or financial advice. Please review important decisions with the appropriate professional.
        </p>
      </Section>

      <Section title="Acceptable Use">
        <List>
          <li>Do not try to access another client&apos;s data or interfere with the Platform.</li>
          <li>Automated or bulk use of the Platform requires our written approval.</li>
        </List>
      </Section>

      <Section title="Intellectual Property">
        <p>
          The Platform&apos;s code, design and content belong to {COMPANY}. Your data and the reports made from it
          belong to you.
        </p>
      </Section>

      <Section title="Disclaimer of Warranties">
        <p>
          The Platform is provided &ldquo;as is&rdquo; without warranties of any kind. We do not guarantee uptime,
          accuracy, or availability.
        </p>
      </Section>

      <Section title="Limitation of Liability">
        <p>
          To the extent the law allows, {COMPANY} is not liable for indirect, incidental, or consequential damages from
          your use of the Platform, and our total liability is limited to what you paid us in the 12 months before the
          claim.
        </p>
      </Section>

      <Section title="Changes to These Terms">
        <p>We may update these Terms. Continued use of the Platform after a change means you accept it.</p>
      </Section>

      <Section title="Contact">
        <p>
          Questions? Email <Email subject="Terms question" />.
        </p>
      </Section>
    </LegalPage>
  );
}
