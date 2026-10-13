// Small, reusable building blocks for the wizard screens.
// Keeping them here means every step looks consistent and changes to the
// look (spacing, colors) happen in one file.

"use client";

import type { ButtonHTMLAttributes, ReactNode } from "react";

export function StepHeader({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <div className="mb-6 space-y-1">
      <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
      {subtitle && <p className="text-ink-muted">{subtitle}</p>}
    </div>
  );
}

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <div className={`rounded-2xl border border-line bg-surface p-5 shadow-[var(--shadow-card)] ${className}`}>{children}</div>
  );
}

/** A large clickable answer option (used for the questionnaire choices). */
export function ChoiceCard(props: {
  label: string;
  description?: string;
  selected: boolean;
  badge?: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={props.onClick}
      aria-pressed={props.selected}
      className={`w-full rounded-2xl border p-4 text-left shadow-[var(--shadow-card)] transition-colors ${
        props.selected ? "border-brand bg-brand-soft" : "border-line bg-surface hover:border-brand"
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="font-medium">{props.label}</span>
        {props.badge && (
          <span className="rounded-full border border-line px-2 py-0.5 text-xs text-ink-muted">{props.badge}</span>
        )}
      </div>
      {props.description && <p className="mt-1 text-sm text-ink-muted">{props.description}</p>}
    </button>
  );
}

type Variant = "primary" | "secondary";

export function Button({
  variant = "primary",
  className = "",
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant }) {
  const styles =
    variant === "primary"
      ? "bg-brand text-on-brand hover:bg-brand-strong disabled:opacity-50"
      : "border border-line bg-surface hover:border-brand hover:text-brand disabled:opacity-50";
  return (
    <button
      type="button"
      {...rest}
      className={`rounded-full px-5 py-2 text-sm font-semibold transition-colors disabled:cursor-not-allowed ${styles} ${className}`}
    />
  );
}

/** Back / Next row at the bottom of each step. */
export function NavRow({ onBack, children }: { onBack?: () => void; children?: ReactNode }) {
  return (
    <div className="mt-8 flex items-center justify-between gap-3">
      {onBack ? (
        <Button variant="secondary" onClick={onBack}>
          Back
        </Button>
      ) : (
        <span />
      )}
      <div className="flex gap-3">{children}</div>
    </div>
  );
}

export function ErrorBox({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <div role="alert" className="mt-4 rounded-xl border border-bad/40 bg-bad/10 px-4 py-3 text-sm text-bad">
      {message}
    </div>
  );
}

/** A labeled number tile (e.g. "Duplicates removed: 12"). */
export function Stat({ label, value, tone }: { label: string; value: string; tone?: "good" | "warn" | "bad" }) {
  const color = tone === "good" ? "text-good" : tone === "warn" ? "text-warn" : tone === "bad" ? "text-bad" : "";
  return (
    <div className="rounded-2xl border border-line bg-surface p-4 shadow-[var(--shadow-card)]">
      <div className="text-xs uppercase tracking-wide text-ink-muted">{label}</div>
      <div className={`mt-1 font-mono text-2xl font-semibold tabular-nums ${color}`}>{value}</div>
    </div>
  );
}

/** "Step 2 of 6" progress bar. */
export function Progress({ step, total, labels }: { step: number; total: number; labels: string[] }) {
  return (
    <nav aria-label="Progress" className="mb-8">
      <ol className="flex gap-1.5">
        {labels.map((label, i) => (
          <li key={label} className="flex-1">
            <div className={`h-1.5 rounded-full ${i <= step ? "bg-brand" : "bg-line"}`} />
            <span className={`mt-1.5 hidden text-xs sm:block ${i === step ? "font-medium" : "text-ink-muted"}`}>
              {label}
            </span>
          </li>
        ))}
      </ol>
      <p className="mt-2 text-xs text-ink-muted sm:hidden">
        Step {step + 1} of {total}: {labels[step]}
      </p>
    </nav>
  );
}

/** Call-to-action box that sends people to the consultant's booking page. */
export function ConsultantCta({ bookingUrl, children }: { bookingUrl: string; children: ReactNode }) {
  return (
    <div className="rounded-2xl border border-brand/40 bg-brand-soft p-5">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="text-sm">{children}</div>
        <a
          href={bookingUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="shrink-0 rounded-full bg-brand px-5 py-2 text-center text-sm font-semibold text-on-brand hover:bg-brand-strong"
        >
          Book a free consultation
        </a>
      </div>
    </div>
  );
}
