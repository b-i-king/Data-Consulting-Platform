// "/enterprise-agreement" — the Enterprise Agreement an enterprise client's owner
// accepts in the app (Settings -> Account -> Enterprise plan; the version, time
// and typed name are saved on their account: api/services/enterprise.py).
// The version here MUST match AGREEMENT_VERSION in api/services/enterprise.py —
// change both together, and every client is asked to accept the new version.
// A starting draft: have a lawyer review it before the first enterprise client.

import type { Metadata } from "next";
import Link from "next/link";
import { COMPANY, LegalPage, List, Section, Strong } from "@/components/legal/LegalPage";

export const metadata: Metadata = { title: "Enterprise Agreement" };

// Same value as AGREEMENT_VERSION in api/services/enterprise.py (ISO format there).
const VERSION = "2026-10-23";

export default function EnterpriseAgreementPage() {
  return (
    <LegalPage title="Enterprise Agreement" updated="10/23/2026">
      <p className="text-sm text-ink-muted">Version {VERSION}</p>

      <Section title="What This Covers">
        <p>
          This agreement is between {COMPANY} (&ldquo;we&rdquo;) and the organization whose authorized representative
          accepts it (&ldquo;you&rdquo;). It applies to your <Strong>enterprise plan</Strong>: the price, contract dates,
          services and limits shown in your workspace under Settings → Account → Enterprise plan (the
          &ldquo;Order&rdquo;). Our <Link href="/terms" className="underline">Terms of Service</Link>,{" "}
          <Link href="/privacy" className="underline">Privacy Policy</Link> and, if you store data with us, the{" "}
          <Link href="/storage-terms" className="underline">Managed Storage Terms</Link> also apply. If they conflict,
          this agreement and your Order win.
        </p>
      </Section>

      <Section title="Services and Limits">
        <List>
          <li>You get the services listed in your Order for the contract dates, with the limits listed there (for example upload size, team seats and rows read from your databases).</li>
          <li>Changes to the Order (services, limits, price) are agreed in writing (email is fine) and appear in your workspace.</li>
          <li>Consulting work beyond the Order (custom dashboards, implementation) is quoted separately.</li>
        </List>
      </Section>

      <Section title="Fees and Invoices">
        <List>
          <li>You pay the price and any one-time setup fee in your Order. We invoice you directly; invoices are due within 30 days unless your Order says otherwise.</li>
          <li>Prices exclude taxes, which you pay where they apply.</li>
          <li>Late or unpaid invoices may lead us to pause the services after written notice.</li>
        </List>
      </Section>

      <Section title="Term, Renewal and Ending">
        <List>
          <li>The agreement runs for the contract dates in your Order. Renewals are agreed in writing.</li>
          <li>Either side may end it if the other materially breaches it and doesn&apos;t fix the breach within 30 days of written notice.</li>
          <li>When it ends, the enterprise services stop. You can export your stored data for 30 days; after that it is deleted as described in the Managed Storage Terms.</li>
        </List>
      </Section>

      <Section title="Your Data">
        <List>
          <li>Your data stays yours. We use it only to provide the services to you.</li>
          <li>Data we read where it lives (your databases, warehouses and apps) is not copied or kept. Data you choose to store is handled under the Managed Storage Terms.</li>
          <li>Health information is handled only under a separate Business Associate Agreement.</li>
        </List>
      </Section>

      <Section title="Confidentiality and Security">
        <p>
          Each side keeps the other&apos;s non-public information confidential and uses it only for this agreement. We
          protect your data with reasonable technical and organizational safeguards (encryption, access controls,
          two-step sign-in for staff, access logs) and will tell you without undue delay if we learn of a breach
          affecting it.
        </p>
      </Section>

      <Section title="Consultants">
        <p>
          Consultants on the platform are independent contractors screened by us. Work they do for you under your Order
          is covered by this agreement&apos;s confidentiality and data terms.
        </p>
      </Section>

      <Section title="Warranties and Liability">
        <List>
          <li>The services are provided with reasonable skill and care. Analysis results depend on the data you provide; please review important decisions with your team or consultant.</li>
          <li>Except for confidentiality breaches or amounts owed, neither side is liable for indirect or consequential losses, and each side&apos;s total liability is limited to the fees paid or payable in the 12 months before the claim.</li>
        </List>
      </Section>

      <Section title="Accepting This Agreement">
        <p>
          It is accepted in the app by typing your name and ticking &ldquo;I agree.&rdquo; By doing so you confirm you
          are authorized to bind your organization. We record the version, the date and time, and the name typed.
        </p>
      </Section>
    </LegalPage>
  );
}
