// ── API response types ────────────────────────────────────────────────────────
// These TypeScript types mirror the JSON the FastAPI backend returns.
// If you change a response in `api/`, update the matching type here so the
// editor and `npm run typecheck` catch mismatches.

export interface Choice {
  id: string;
  label: string;
  description?: string;
  available?: boolean;
  /** A real integration ("supabase", "quickbooks", "xero"): the wizard takes the connect path for it. */
  connector?: string;
}

export interface DataLocation extends Choice {
  available: boolean;
  options: Choice[];
}

export interface Service {
  id: string;
  name: string;
  description: string;
  /** "hourly" = paid consulting from the first hour (Data Strategy & Discovery). */
  pricing_model: "runtime" | "package" | "consultation" | "hourly" | "early_access";
  starting_price_usd: number;
  automated: boolean;
  requires_consultant: boolean;
  includes: string[];
  /** false = not sold yet; shown with an "Early access" badge. */
  available: boolean;
  /** Client-friendly names of the skills behind this service (data_skills/catalog.json). */
  capabilities: string[];
  /** Free 30-minute consultation (or paid from the start), then the hourly rate. */
  consultation: ConsultationTerms;
  /** Do-it-yourself price as a package tool; null = not a tool (Data Strategy & Discovery). */
  tool_monthly_usd: number | null;
  tool_annual_usd: number | null;
  /** Tools it needs in the package (AI: ["data_engineering"]). */
  tool_requires: string[];
}

export interface FileFormat {
  id: string;
  label: string;
  extensions: string[];
  group: string;
  recommended: boolean;
  note: string;
}

export interface Subsector {
  code: string; // 3-digit NAICS, e.g. "445"
  name: string;
}

export interface Sector {
  code: string; // 2-digit NAICS, e.g. "44-45"
  name: string;
  supersector: Supersector;
  subsectors: Subsector[];
}

export type Supersector = "goods_producing" | "service_providing";

/** Business profile answers (all optional; each industry answer narrows the previous). */
export interface Industry {
  industry_supersector: Supersector | null;
  naics_sector: string | null;
  naics_subsector: string | null;
  /** LinkedIn company size code "A"–"I" (e.g. "C" = 11–50 employees). */
  company_size: string | null;
  /** "Other": what they typed when nothing in the list fit (saved instead of a NAICS code). */
  industry_other?: string | null;
}

/** One employee-count band (LinkedIn company size codes). */
export interface CompanySize {
  id: string; // "A"–"I"
  label: string; // "11–50 employees"
  employees: string; // "11-50"
}

/** GET /api/intake/options */
export interface IntakeOptions {
  account_types: Choice[];
  data_locations: DataLocation[];
  services: Service[];
  supported_extensions: string[];
  file_formats: FileFormat[];
  /** extension -> reason it is refused (e.g. pickle can run code) */
  excluded_formats: Record<string, string>;
  industries: {
    source: string;
    supersectors: { id: Supersector; label: string; description: string }[];
    sectors: Sector[];
  };
  /** Business size bands, LinkedIn company size codes A–I. */
  company_sizes: CompanySize[];
  limits: { max_upload_mb: number; max_files_per_upload: number };
  pricing: { price_per_runtime_minute_usd: number; billing_increment_seconds: number };
  booking_url: string;
  /** The plans on sale, each with a monthly and a yearly price. */
  subscriptions: Subscription[];
  /** Consulting after the free 30-minute first consultation (default $80). */
  consulting_rate_per_hour_usd: number;
  /** A consultant's one-time setup fee and ongoing-help prices (pricing.consulting_catalog). */
  consulting?: ConsultingCatalog;
  /** The default consultant ("I'd like Brandon to do this for me"); null when none is set up. */
  consultant: { name: string } | null;
  /** Every consultant and how full they are (the "pick your consultant" fill bars). */
  consultants: ConsultantLoad[];
}

/**
 * One consultant in the picker (GET /api/accounts/consultants, also in the
 * intake options): first name and how full they are — never an email.
 */
export interface ConsultantLoad {
  id: string;
  name: string;
  /** How many clients they take at once (they choose, 1 to 10; default 3). */
  capacity: number;
  clients: number;
  /** clients / capacity × 100, 0–100: green under 50, yellow from 50, red at 100. */
  percent: number;
  full: boolean;
  /** The default consultant: clients who pick nobody go here first. */
  is_default: boolean;
}

/** The choice at the bottom of the sign-up form. (Consultants apply AFTER signing up.) */
export type SignupAs = "client" | "admin";

/** Why an application wasn't approved (consultants.DENIAL_REASONS; the applicant sees the text). */
export type DenialReason =
  | "identity_unverified" | "not_enough_experience" | "skills_mismatch" | "work_sample" | "conduct" | "incomplete" | "not_adding";

/** Hands-on data experience on the application (consultants.EXPERIENCE_RANGES). */
export type ExperienceRange = "lt_1" | "1_3" | "3_5" | "5_10" | "10_plus";

/** POST /api/accounts/consultant-request — the consultant screening application. */
export interface ConsultantApplicationInput {
  /** Service ids they can deliver (of the seven, at least one). */
  services: string[];
  /** Hands-on data experience, a range id ("" until picked). */
  experience: ExperienceRange | "";
  /** "SQL, pandas, Power BI". */
  tools: string;
  /** REQUIRED: their LinkedIn profile (https://www.linkedin.com/in/...). */
  linkedin_url: string;
  /** Optional: a portfolio, GitHub, a report or a dashboard, https:// ("" = none). */
  work_sample_url: string;
  /** A data project they delivered (100+ characters). */
  project_summary: string;
  /** The code of conduct; must be true. */
  agree_conduct: boolean;
}

/** GET /api/accounts/consultant-application — may I apply, and what happened last time. */
export interface ConsultantApplicationStatus {
  can_apply: boolean;
  /** Why not: a consultant already, an application waiting, the waiting period, or not invited. */
  blocked: "already" | "pending" | "waiting" | "not_invited" | null;
  /** Plain-English reason (e.g. "You can apply again on 01/18/2027."). */
  message: string | null;
  mode: "invite" | "open";
  invited: boolean;
  /** Waiting period: the first day they may apply again (YYYY-MM-DD). */
  reapply_after: string | null;
  latest: {
    status: "pending" | "approved" | "denied";
    submitted_at: string;
    decided_at: string | null;
    denial_reason: DenialReason | null;
    denial_reason_text: string | null;
    /** The admin's personal feedback. */
    decision_note: string | null;
    /** With a denial: a real person decided, and it's a judgment call. */
    human_review_note: string | null;
  } | null;
  code_of_conduct: string[];
  /** The experience choices, in order. */
  experience_ranges: { id: ExperienceRange; label: string }[];
}

/**
 * GET/PUT /api/consultant/settings — a consultant's own Settings page:
 * capacity, their Cal.com booking links, and their Cal.com webhook.
 */
export interface ConsultantSettings {
  load: ConsultantLoad;
  max_capacity: number;
  /** Their free 30-minute Cal.com link (their clients see it). */
  free_booking_url: string | null;
  /** Their paid session link (paid on their own Cal.com). */
  paid_booking_url: string | null;
  /** path: add the API address in front for Cal.com's "Subscriber URL"; secret: null until made. */
  webhook: { path: string; secret: string | null };
  /** The onboarding checklist after approval. */
  onboarding: ConsultantOnboarding;
  /** Stripe payout account (clients' setup and ongoing-help payments reach them through it). */
  payouts: { needed: boolean; connected: boolean; ready: boolean; test_mode: boolean };
  /** True until every REQUIRED onboarding step is done (the "finish your onboarding" nudge). */
  setup_needed: boolean;
}

/** What a consultant's help costs (GET /api/intake/options "consulting", pricing.consulting_catalog). */
export interface ConsultingCatalog {
  /** The one-time setup fee. */
  setup_usd: number;
  /** Ongoing help's hourly rate (the tool does most of the work). */
  hourly_usd: number;
  /** Hours a week when Data Cleaning and/or Data Engineering is picked (counted once). */
  core_hours: number;
  /** Hours a week for each other service picked. */
  service_hours: number;
  /** Never more than this a week. */
  max_hours: number;
  core_services: string[];
  light_services: string[];
  /** A year of ongoing help costs this many months. */
  annual_months: number;
  /** Hourly sessions booked on the consultant's Cal.com (paid to them directly). */
  session_rate_usd: number;
  /** The smallest ongoing help (one other service), a month. */
  min_monthly_usd: number;
}

/** One thing a client paid their consultant for (dim.consultant_engagements). */
export interface ConsultingEngagement {
  id: string;
  kind: "setup" | "retainer";
  hours_per_week: number | null;
  /** Ongoing help: the services they get help with. */
  services: string[];
  /** Why it ended: "client", or early because the consultant left / was deactivated. */
  ended_reason: "client" | "consultant_deactivated" | "consultant_left" | "account_deleted" | null;
  consultant_name: string | null;
  billing_interval: "month" | "year" | null;
  amount_usd: number;
  status: string;
  cancel_at_period_end: boolean;
  current_period_end: string | null;
  created_at: string | null;
}

/** GET /api/billing/consulting — Plan & billing -> "Your consultant". */
export interface ConsultingOverview {
  catalog: ConsultingCatalog;
  /** What they can pick for ongoing help (tools in their package + Strategy & Discovery), with the hours each adds. */
  services: { id: string; name: string; hours: number }[];
  consultant: { name: string } | null;
  can_buy: boolean;
  /** Why not (no consultant chosen, or they're still setting up payouts). */
  reason: string | null;
  engagements: ConsultingEngagement[];
  /** The running ongoing help, if any. */
  retainer: ConsultingEngagement | null;
  /** Ongoing help that ended early because their consultant left or was deactivated. */
  ended: ConsultingEngagement | null;
  /** "Cancel any time; paid to the end of the period..." — must be accepted before paying. */
  cancel_terms: string;
  test_mode: boolean;
}

/** Admin -> Prices -> "Consultant help" (GET/PUT /api/admin/consulting-prices). */
export interface ConsultingPrices {
  values: ConsultingPriceValues;
  defaults: ConsultingPriceValues;
  updated_at: string | null;
  /** Data Cleaning + Data Engineering + one more service, at these numbers. */
  example: { hours: number; monthly_usd: number };
}
export interface ConsultingPriceValues {
  setup_usd: number;
  help_rate_usd: number;
  core_hours: number;
  service_hours: number;
  max_hours: number;
  platform_fee_percent: number;
}

/** A newly approved consultant's onboarding (consultants.onboarding). */
export interface ConsultantOnboarding {
  steps: { id: "two_step" | "contractor" | "payouts" | "free_link" | "paid_link" | "webhook" | "capacity"; label: string; done: boolean; required: boolean }[];
  /** Every required step is done. */
  done: boolean;
  /** The independent-contractor terms (pay and taxes), one point per item. */
  contractor_terms: string[];
  contractor_terms_version: string;
  /** ISO date-time, or null until they agree. */
  contractor_agreed_at: string | null;
}

/** How a consultant helps: one analysis of everything sent, or ongoing monthly help. */
export type ConsultantPlan = "one_time" | "monthly";

/** GET /api/admin/consultant-requests — one person who asked to be a consultant. */
export interface ConsultantRequest {
  account_id: string;
  name: string;
  email: string;
  company_name: string | null;
  location: string | null;
  status: "pending" | "approved" | "denied";
  requested_at: string | null; // ISO date-time
  decided_at: string | null;
  /** The screening answers and decision (null for a request from before screening existed). */
  application: {
    services: string[];
    experience: ExperienceRange;
    /** "3–5 years". */
    experience_label: string;
    tools: string;
    linkedin_url: string;
    work_sample_url: string | null;
    project_summary: string;
    submitted_at: string | null;
    /** How many times this person has applied (2+ = applied again after a denial). */
    times_applied: number;
    denial_reason: DenialReason | null;
    decision_note: string | null;
    /** Denied: the first day they may apply again (YYYY-MM-DD). */
    reapply_after: string | null;
  } | null;
}

/** Admin panel: an email that may apply to be a consultant (while applications are invite-only). */
export interface ConsultantInvite {
  email: string;
  /** Private note for admins. */
  note: string | null;
  created_at: string | null;
  /** They already made an account with this email. */
  has_account: boolean;
}

/** Admin panel: one consultant, active or not, with their lifecycle dates (ISO date-times). */
export interface TeamConsultant extends ConsultantLoad {
  email: string;
  active: boolean;
  created_at: string | null;
  updated_at: string | null;
  /** Last time an admin switched them off (kept after reactivating, as history). */
  deactivated_at: string | null;
  deactivation_reason: string | null;
  reactivated_at: string | null;
  /** When they agreed to the independent-contractor terms (null = not yet). */
  contractor_agreed_at: string | null;
  /** They agreed to the CURRENT wording (false after the terms change). */
  contractor_terms_current: boolean;
  /** Every required onboarding step is done. */
  onboarding_done: boolean;
  /** The required onboarding steps still left (labels). */
  onboarding_left: string[];
}

/** Why someone deleted their account (the optional pick in the delete dialog). */
export type DeletionReason = "too_expensive" | "missing_features" | "not_needed" | "switching_tools" | "privacy" | "other";

/** Admin panel: how many accounts were deleted, and why — counts only, never who. */
export interface DeletionSummary {
  total: number;
  /** Deleted in the last `recent_days` days. */
  recent: number;
  recent_days: number;
  /** reason (or "none") -> count. */
  by_reason: Record<string, number>;
  /** Last 6 months, newest first ("2026-10"). */
  by_month: { month: string; count: number }[];
}

/** The Admin panel: the requests, every active consultant's load, the whole team, and deletions. */
export interface AdminPanelData {
  requests: ConsultantRequest[];
  consultants: ConsultantLoad[];
  team: TeamConsultant[];
  deletions: DeletionSummary;
  /** After deactivating / reactivating: what happened ("Kim is deactivated. 2 clients moved."). */
  notice?: string;
  /** HIPAA requests waiting for a decision. */
  hipaa_waiting?: number;
  /** Who may apply to be a consultant: invited emails only, or anyone with an account. */
  applications_mode: "invite" | "open";
  invites: ConsultantInvite[];
  /** The reasons an admin can give when denying (the applicant sees the text). */
  denial_reasons: { id: DenialReason; text: string }[];
  /** Waiting periods (months) to choose from when denying; the first is the minimum. */
  reapply_choices: number[];
  /** A denial's personal feedback must be at least this long. */
  min_feedback_chars: number;
  /** What the applicant reads above a denial (a real person decided; it's subjective). */
  human_review_note: string;
  /** Why clients canceled their package (last 12 months), from Stripe's portal. */
  cancellations?: { total: number; by_reason: Record<string, number>; comments: { at: string | null; reason: string | null; comment: string }[] };
}

/** Admin -> Testers: one plan holder (GET /api/admin/accounts). Dates are ISO date-times. */
export interface AdminPerson {
  id: string;
  name: string;
  email: string;
  company_name: string | null;
  account_type: "individual" | "business";
  role: "admin" | "consultant" | "client";
  /** Tries the paid tools free (packages switch on with no charge, even with Stripe live). */
  is_tester: boolean;
  /** A client's consultant (consultants row id), or null. */
  consultant_id: string | null;
  /** Healthcare client: data access is audited. */
  is_healthcare: boolean;
  mfa_enabled: boolean;
  /** Has a live package (paid, or free for a tester). */
  has_plan: boolean;
  created_at: string | null;
  updated_at: string | null;
  last_login_at: string | null;
}

/** Admin -> Prices: one plan's live and default prices (GET /api/admin/prices). */
export interface PlanPriceRow {
  id: string;
  name: string;
  kind: "tool" | "bundle";
  monthly_usd: number;
  annual_usd: number;
  default_monthly_usd: number;
  default_annual_usd: number;
  /** Free trial on a first package, in days (0 = none). */
  trial_days: number;
  /** False = not charged yet (AI before launch). */
  billable: boolean;
  /** When and by whom it was last changed here; null = still the starting price. */
  updated_at: string | null;
  updated_by: string | null;
}

/** GET /api/admin/prices. stripe: Stripe is on (a change also updates Stripe). */
export interface AdminPrices {
  prices: PlanPriceRow[];
  stripe: boolean;
}

/** Admin -> Seasonal sales: one yearly window (repeats every year). */
export interface SeasonalSale {
  id: string;
  name: string;
  /** "MM-DD" */
  starts_on: string;
  /** "MM-DD", included (may be before starts_on: wraps the new year). */
  ends_on: string;
  percent_off: number;
  active: boolean;
  updated_at: string | null;
  updated_by: string | null;
}

/** GET/PUT /api/admin/sales. running = the sale on today, if any. */
export interface AdminSales {
  sales: SeasonalSale[];
  running: RunningSale | null;
}

/** PUT /api/admin/prices/{plan}: the refreshed list, and what changed in Stripe. */
export interface PriceChangeResult {
  prices: PlanPriceRow[];
  stripe: { enabled: boolean; changes: string[] };
}

/**
 * One priced item = a Stripe product with a monthly and an annual price: a TOOL
 * (build your own package; the total is the sum) or the All tools BUNDLE.
 */
export interface Subscription {
  id: string; // "starter", "growth", "all_tools" (bundles); "data_cleaning", ..., "ai" (tools)
  kind: "tool" | "bundle";
  /** Data Cleaning: in every package. */
  base: boolean;
  /** Tools that must be picked too (AI needs "data_engineering"). */
  requires: string[];
  /** false = free until it launches (AI). */
  billable: boolean;
  /** Bundle only: how much less per month than picking its tools one by one. */
  bundle_savings_month_usd: number;
  /** Bundle only: the tools it includes (never bought twice; others can be added on). */
  covers?: string[];
  name: string;
  description: string;
  monthly_usd: number;
  annual_usd: number;
  annual_per_month_usd: number;
  annual_savings_usd: number;
  annual_savings_pct: number;
  includes: string[];
  /** Services the plan unlocks. */
  service_ids: string[];
  /** Free trial a FIRST package with this tool gets, in days (0 = none). Set in Admin -> Prices. */
  trial_days: number;
}

/** A seasonal sale running today (dim.seasonal_sales): percent off a first package's first payment. */
export interface RunningSale {
  id: string;
  name: string;
  percent_off: number;
  /** "MM-DD" (repeats every year). */
  ends_on: string;
  /** This season's last day, ISO "YYYY-MM-DD". */
  ends: string;
}

/** What a first package gets right now (api/services/offers.py): a trial, else the sale. */
export interface FirstPackageOffer {
  /** The longest trial among the tools (0 when not eligible). */
  trial_days: number;
  /** False once they've had a trial or any package (one trial per account). */
  trial_eligible: boolean;
  sale: RunningSale | null;
  applies: "trial" | "sale" | null;
}

export type BillingInterval = "month" | "year";
/** A "build your own" package: the tools picked (or ["all_tools"]), and the billing interval. */
export interface SubscriptionChoice {
  plans: string[];
  interval: BillingInterval;
}

/** One live subscription (table `subscriptions`, kept in sync by Stripe). */
export interface LiveSubscription {
  id: string;
  /** The tools in it (or ["all_tools"]). */
  plan_ids: string[];
  names: string[];
  interval: BillingInterval;
  status: string;
  /** What it costs per interval. */
  total_usd: number;
  /** Canceled, but paid through current_period_end. */
  cancel_at_period_end: boolean;
  current_period_end: string | null; // YYYY-MM-DD
}

/** Talking to a consultant for one service. */
export interface ConsultationTerms {
  free_minutes: number; // 30, or 0 for Data Strategy & Discovery
  rate_per_hour_usd: number;
  text: string; // "Free 30-minute consultation, then $80/hour"
}

/** POST /api/billing/checkout. url = Stripe's page; null in test mode. */
export interface CheckoutResult {
  url: string | null;
  activated: boolean;
  /** An existing subscription's tools were changed in place. */
  updated: boolean;
  test_mode: boolean;
  /** The free trial it started with (0 = none). */
  trial_days: number;
  /** The seasonal sale applied to the first payment, if any. */
  sale: RunningSale | null;
}

// ── /api/files/analyze ────────────────────────────────────────────────────────

export interface DatasetSummary {
  name: string;
  source_file: string;
  file_type: string;
  rows: number;
  columns: string[];
  entity: string;
  years_from_name: number[];
  years_from_data: number[];
  group_id: number;
}

export interface DatasetGroup {
  group_id: number;
  label: string;
  entity: string;
  structure: "single" | "same_schema" | "partitioned_by_year";
  members: string[];
  shared_columns: string[];
  years: number[];
  recommendation: string;
}

/**
 * Stack or join other tables of the upload with the one being loaded
 * (api/services/combine.py). union = append rows; join = add the other
 * table's columns by a shared key (left keeps every row, inner only matches).
 */
export interface CombineSpec {
  mode: "union" | "join";
  groups: number[];
  left_on?: string;
  right_on?: string;
  how?: "left" | "inner";
}

/** GET /api/accounts/consultants — one page of the consultant directory. */
export interface ConsultantPage {
  consultants: ConsultantLoad[];
  page: number;
  page_size: number;
  total: number;
  pages: number;
}

export interface Relationship {
  left_group: number;
  right_group: number;
  key_columns: string[];
  description: string;
  /** "key" = exact shared ID; "fuzzy" = matched by name / email / phone */
  method: "key" | "fuzzy";
}

export interface ColumnReport {
  column: string;
  inferred_type: string;
  missing_count: number;
  missing_pct: number;
  unique_count: number;
  example_values: string[];
  /** Missing-value advice: under 20% "low", 20%+ "review", 90%+ "remove". */
  missing_level: "none" | "low" | "review" | "remove";
  recommendation: string;
  /** Suggestion: mean (symmetric numbers), median (skewed numbers), mode (text),
   *  or "drop_rows" for an ID column (remove the rows with no ID). */
  suggested_fill: FillMethod | "drop_rows" | null;
  skewness: number | null;
  /** method -> the value it would fill in, e.g. { mean: "12.4", median: "9" } */
  fill_options: Partial<Record<FillMethod, string>>;
  /** What was done: keep, dropped (the column), rows_dropped (rows with no ID), or the fill method used. */
  action: "keep" | "dropped" | "rows_dropped" | FillMethod;
  filled_count: number;
  /** Rows removed because this column was blank ("drop_rows"). */
  rows_dropped: number;
  /** Looks like an ID or code: never filled, and shouldn't be blank. */
  is_id: boolean;
  /** Stable name for choices and renames (the automatic cleaned name). */
  key: string;
  /** The header in the uploaded file ("" when it was blank). */
  original_name: string;
  /** Header problem still to fix: a blank header, or a name shared with another column. */
  name_problem: "unnamed" | "duplicate" | null;
  /** "Wrong data": values far outside the usual range (3 × IQR), or dates in the future / before 1900. */
  outliers: OutlierCheck | null;
  /** "Wrong format": a text column that is really numbers or dates. */
  format_issue: FormatCheck | null;
  missing_after: number;
}

export interface OutlierCheck {
  kind: "number" | "date";
  count: number;
  /** The expected range (numbers for number columns, YYYY-MM-DD for dates). */
  low: number | string;
  high: number | string;
  min: string;
  max: string;
  /** The most extreme unusual values. */
  examples: string[];
  note: string;
}

export interface FormatCheck {
  to: "number" | "date";
  convertible: number;
  /** Values that wouldn't convert (they'd become blank). */
  failing: number;
  examples: string[];
}

/** One transform step (api/services/transforms.py), like Power Query's "Applied steps". */
export type TransformStep =
  | { op: "promote_headers" }
  | { op: "remove_top_rows"; count: number }
  | { op: "change_type"; column: string; to: "number" | "date" | "text" }
  | { op: "replace_values"; column: string; find: string; replace: string }
  | { op: "outliers"; column: string; method: "cap" | "remove" | "blank"; low?: number | string | null; high?: number | string | null }
  | { op: "unpivot"; columns: string[]; attribute?: string; value?: string }
  | { op: "pivot"; column: string; values: string; agg: "sum" | "mean" | "count" | "first" };

/** What one step did (report.transforms_applied). `step` counts from 1. */
export interface AppliedStep {
  step: number;
  op: TransformStep["op"];
  column?: string;
  count: number;
  text: string;
}

export type FillMethod = "mean" | "median" | "mode";
/** A choice for one column's missing values ("auto" = our suggestion). */
export type ColumnAction = "keep" | "drop" | "auto" | "drop_rows" | FillMethod;
/** group id -> column key -> choice; sent to analyze, download and skills alike. */
export type ColumnActions = Record<string, Record<string, ColumnAction>>;
/** group id -> column key -> the client's own name for it (cleaned to snake_case by the server). */
export type ColumnNames = Record<string, Record<string, string>>;
/** group id -> the table's transform steps, in order. */
export type Transforms = Record<string, TransformStep[]>;

/** Everything needed to re-create the cleaned data on the server. */
export interface CleaningInput {
  files: File[];
  strategy: MissingStrategy;
  columnActions: ColumnActions;
  columnNames: ColumnNames;
  transforms: Transforms;
}

export interface CleaningReport {
  dataset_name: string;
  rows_before: number;
  rows_after: number;
  columns: number;
  blank_strings_converted: number;
  empty_rows_removed: number;
  duplicate_rows_removed: number;
  rows_dropped_for_missing: number;
  missing_strategy: MissingStrategy;
  total_missing_cells: number;
  total_missing_pct: number;
  columns_fully_empty: string[];
  column_reports: ColumnReport[];
  notes: string[];
  runtime_seconds: number;
  columns_before: number;
  columns_dropped: string[];
  cells_filled: number;
  missing_pct_before: number;
  columns_to_review: string[];
  columns_to_remove: string[];
  /** 90%+ of all cells are missing: suggest a consultation. */
  consultation_recommended: boolean;
  /** At least 30 rows before AND after cleaning; otherwise no dashboard or analysis skills. */
  analysis_ready: boolean;
  /** Why the table can't be analyzed (empty when it can). */
  analysis_note: string;
  /** Column names cleaned to lowercase snake_case: original name -> new name. */
  columns_renamed: Record<string, string>;
  /** Rows removed because an ID was blank (listed on the Excel download's "rows_missing_id" sheet). */
  rows_missing_id_removed: number;
  /** Step 0a: columns of pictures stored in the file, removed (names as in the file). */
  image_columns_removed: string[];
  /** Step 0a: pictures stored in text cells, blanked. */
  image_cells_removed: number;
  /** Columns whose header was blank in the file, not yet named or removed. */
  unnamed_columns: string[];
  /** Groups of columns that had the same name (e.g. ["amount", "amount_2"]), not yet told apart. */
  duplicate_columns: string[][];
  /** What each transform step did, in order. */
  transforms_applied: AppliedStep[];
  /** Columns with unusual values / in the wrong format. */
  outlier_columns: string[];
  format_columns: string[];
}

export interface SampleSizeCheck {
  question: string;
  rows_needed: number;
  rows_available: number;
  verdict: "green" | "yellow" | "red";
  note: string;
}

export interface SampleSize {
  rows: number;
  columns: number;
  unique_rows: number;
  complete_rows: number;
  incomplete_row_pct: number;
  memory_mb: number;
  time_span: { column: string; first: string; last: string; days_covered: number; rows_per_day: number } | null;
  regression_predictor_guidance: string;
  checks: SampleSizeCheck[];
  warnings: string[];
}

export type Cell = string | number | boolean | null;

/** Wait + charge for downloading one table (api/services/pricing.py download_estimate). */
export interface DownloadEstimate {
  rows: number;
  columns: number;
  /** rows × fields — what the wait is based on. */
  cells: number;
  /** The processing charge the client confirms; the download never charges more. */
  amount_usd: number;
  billable_minutes: number;
  formats: Record<"csv" | "xlsx", { estimated_seconds: number; label: string; under_five_minutes: boolean }>;
}

export interface GroupOutput {
  group_id: number;
  label: string;
  report: CleaningReport;
  sample_size: SampleSize;
  download_estimate: DownloadEstimate;
  preview: {
    columns: string[];
    rows: Record<string, Cell>[];
    total_rows: number;
    truncated: boolean;
  };
}

export interface RuntimeCost {
  runtime_seconds: number;
  billing_increment_seconds: number;
  billable_minutes: number;
  rate_per_minute_usd: number;
  amount_usd: number;
}

export interface AnalyzeResponse {
  categorization: {
    mode: "single_file" | "multiple_files";
    datasets: DatasetSummary[];
    groups: DatasetGroup[];
    relationships: Relationship[];
    next_step: string;
  };
  groups: GroupOutput[];
  runtime: RuntimeCost;
}

export type MissingStrategy = "keep" | "drop_any";

// ── /api/skills ───────────────────────────────────────────────────────────────

export interface SkillParam {
  type: "str" | "int" | "float" | "bool" | "column" | "columns" | "choice" | "json";
  default: unknown;
  help: string;
  choices: string[];
  api_allowed: boolean;
}

export interface SkillInfo {
  id: string;
  title: string;
  description: string;
  input: "table" | "tables";
  wants_raw: boolean;
  category: string;
  category_name: string;
  label: string;
  /** The skill its service's workspace page opens on (one per service). */
  primary: boolean;
  params: Record<string, SkillParam>;
}

export interface LineChartSpec {
  type: "line";
  title: string;
  x: string[];
  series: { name: string; values: (number | null)[] }[];
}

export interface BarChartSpec {
  type: "bar";
  title: string;
  categories: string[];
  values: number[];
  value_label: string;
}

// Chart specs built by data_skills/_core.py (histogram_chart, box_stats/box_chart,
// scatter_chart, heatmap_chart); drawn by web/components/charts/ChartView.tsx
// (the EDA report view, web/components/explore/EdaReport.tsx, uses it too).
export interface HistogramChartSpec {
  type: "histogram";
  title: string;
  /** Bin boundaries, one more than `counts`: bin i covers bin_edges[i] to bin_edges[i + 1]. */
  bin_edges: number[];
  counts: number[];
  x_label: string;
  y_label: string;
}

/** One box of a box plot (1.5 × IQR rule). */
export interface BoxStats {
  name: string;
  n: number;
  whisker_low: number;
  q1: number;
  median: number;
  q3: number;
  whisker_high: number;
  lower_fence: number;
  upper_fence: number;
  /** The most extreme outliers (at most 50); `outlier_count` is the full number. */
  outliers: number[];
  outlier_count: number;
}

export interface BoxChartSpec {
  type: "box";
  title: string;
  boxes: BoxStats[];
  value_label: string;
  group_label: string;
}

export interface ScatterChartSpec {
  type: "scatter";
  title: string;
  points: { x: number; y: number }[];
  x_label: string;
  y_label: string;
}

export interface HeatmapChartSpec {
  type: "heatmap";
  title: string;
  rows: string[];
  columns: string[];
  /** One list per row; values in [-1, 1], null = no value. */
  values: (number | null)[][];
  min: number;
  max: number;
  value_label: string;
}

export type ChartSpec = LineChartSpec | BarChartSpec | HistogramChartSpec | BoxChartSpec | ScatterChartSpec | HeatmapChartSpec;

// eda-report's `data.report` (see data_skills/eda-report/skill.py)
export interface EdaReportSection {
  id: string;
  /** The headline, e.g. "Missing Values". */
  title: string;
  /** Plain-English paragraph explaining the charts for this data. */
  explanation: string;
  bullets: string[];
  charts: ChartSpec[];
  /** Name of a table in SkillResult.tables to show in this section, if any. */
  table: string | null;
  /** False when the section could not apply (the explanation says why). */
  applicable: boolean;
}

export interface EdaReport {
  title: string;
  subtitle: string;
  sections: EdaReportSection[];
}

export interface SkillResult {
  skill: string;
  summary: string;
  findings: string[];
  tables: Record<string, Record<string, Cell>[]>;
  charts: ChartSpec[];
  artifacts: Record<string, string>;
  data: Record<string, unknown>;
  runtime_seconds: number;
  /** True when the tool isn't in the package: a small preview (no charts, tables or downloads). */
  preview?: boolean;
  /** The service the preview is of, e.g. "data_visualization". */
  preview_service?: string;
  /** "This is a one-time preview of ... Add ... to your package ...". */
  preview_note?: string;
  /** How much the full result has that the preview doesn't show. */
  preview_hidden?: { findings: number; tables: number; charts: number; files: number };
}

// build-dashboard's `data` payload (see data_skills/build-dashboard/skill.py)
export interface PeriodRow {
  period: string; // YYYY-MM-DD (start of the day / week / month)
  value: number | null;
  previous: number | null;
  change: number | null;
  pct_change: number | null;
  partial: boolean;
  /** Matching period of the PREVIOUS range, by position (the comparison line). */
  prior_range: number | null;
  prior_period: string | null;
}

export interface Kpi {
  current: number;
  previous?: number;
  change?: number;
  pct_change?: number | null;
}

export interface DashboardData {
  title: string;
  date_column: string;
  /** How each metric is worked out, as the equivalent SQL (the "How this was calculated" panel, QueryPanel.tsx). */
  lineage?: {
    date_column: string;
    metrics: Record<string, {
      label: string;
      /** SQL, e.g. SUM("amount"); `where` an extra condition (a KPI type) or null. */
      expression: string;
      where: string | null;
      /** The pandas twin, on a group `g` (e.g. g['amount'].sum()); `pandas_where` the condition on `df`. */
      pandas: string;
      pandas_where: string | null;
      fields: string[];
      note: string | null;
    }>;
  };
  /** "kpi_types": KPIs are the most common values of a type column; "metrics": number columns. */
  mode: "kpi_types" | "metrics";
  kpi_column: string | null;
  kpi_column_label: string | null;
  value_column: string | null;
  value_label: string;
  /** Metric ids of the KPI cards, one line chart each. */
  kpis: string[];
  /** Metric ids of the overview cards (total value, transactions). */
  overview: string[];
  metrics: string[];
  metric_labels: Record<string, string>;
  /** Customer / demographic columns offered as filters, and their values. */
  filter_columns: string[];
  filter_labels: Record<string, string>;
  filter_options: Record<string, string[]>;
  /** Filters that were applied to this result. */
  filters: Record<string, string[]>;
  rows: number;
  dimension_column: string | null;
  breakdown_metric: string;
  as_of: string;
  first_date: string;
  default_range: string;
  default_comparison: string;
  range_labels: Record<string, string>;
  comparison_labels: Record<string, string>;
  /** Short labels for narrow screens: 4W, 3M, 1Y, MTD, YTD, All · DoD, WoW, MoM. */
  range_short_labels: Record<string, string>;
  comparison_short_labels: Record<string, string>;
  /** Ranges the data supports, e.g. ["last_1_week"] for one week of data ("all" needs > 1 year). */
  available_ranges: string[];
  /** Comparisons the data supports: dod needs 2 days, wow 2 weeks, mom 2 months. */
  available_comparisons: string[];
  days_of_data: number;
  ranges: Record<
    string,
    {
      window: { start: string; end: string; prev_start: string | null; prev_end: string | null };
      kpis: Record<string, Kpi>;
      series: Record<string, Record<string, PeriodRow[]>>;
    }
  >;
  breakdown: Record<string, BreakdownRow[]>;
  /** Line under the title. */
  subtitle?: string;
  /** Above the fold: the KPI row (1–4 cards), then container rows (data_skills/build-dashboard/layout.py). */
  layout: DashboardLayout;
  /** Each category column's top values per range: breakdowns[column][range]. */
  breakdowns: Record<string, Record<string, BreakdownRow[]>>;
  /** How to show each metric's numbers. */
  metric_formats: Record<string, "number" | "currency" | "percent" | "days">;
  /** "lower" = a drop is good news (costs, days in A/R); colors changes green / gray / red. */
  metric_directions: Record<string, "higher" | "lower">;
  /** Industry benchmarks shown on the KPI card ("60–65% of sales is a sensible target"). */
  metric_benchmarks: Record<string, string>;
  /** Healthy [low, high] band per metric (the gauge card marks it), e.g. prime cost [60, 65]. */
  metric_targets?: Record<string, [number, number]>;
  /** ONE KPI card style for the whole row (layout.py kpi_style). */
  kpi_style?: "number" | "change" | "gauge";
  /** The industry KPIs found in this data (industry_metrics.json); generic = none found. */
  industry: { sector: string | null; name: string | null; metrics: string[]; generic: boolean };
}

export interface BreakdownRow {
  category: string;
  current: number;
  previous: number | null;
  pct_change: number | null;
}

/** One visual in a container: a trend (line) or categories (horizontal bars). */
export interface DashboardVisual {
  kind: "line" | "hbar";
  metric: string;
  /** hbar: the category column. */
  dimension?: string;
  title: string;
}

/** A container row: a pattern from templates/layouts.json, its widths (of 6 columns) and its visuals. */
export interface DashboardRow {
  pattern: "full" | "half_half" | "two_thirds_third" | "third_two_thirds" | "thirds";
  spans: number[];
  items: DashboardVisual[];
}

export interface DashboardLayout {
  kpi_row: string[];
  /** With a single KPI: the trend that shares its row (Canva page 7). */
  kpi_side: DashboardVisual | null;
  rows: DashboardRow[];
}

// ── /api/quote ────────────────────────────────────────────────────────────────

export interface Quote {
  lines: { service_id: string; name: string; pricing_model: string; amount_usd: number; note: string }[];
  estimated_total_usd: number;
  currency: "USD";
  requires_consultant: boolean;
  booking_url: string | null;
}

// ── /api/accounts ─────────────────────────────────────────────────────────────

export type AccountType = "individual" | "business";

export interface Account {
  id: string;
  account_type: AccountType;
  first_name: string;
  last_name: string;
  email: string;
  company_name: string | null;
  team_id: string | null;
  /** Business teams: "owner" (admin), "developer" (changes data) or "member" (view only); null for individuals. */
  team_role: TeamRole | null;
  status: string;
  industry_supersector: Supersector | null;
  naics_sector: string | null;
  naics_subsector: string | null;
  company_size: string | null;
  location_id: number | null;
  /** "Atlanta, Georgia, United States" */
  location: string | null;
  industry_other: string | null;
  /** "consultant" accounts see the "Working on" client picker and every service. */
  role: "client" | "consultant";
  /** Admins (ADMIN_EMAILS) see the Admin panel and approve consultants. */
  is_admin: boolean;
  /** Two-step sign-in is on. */
  mfa_enabled?: boolean;
  /** Admins and consultants must turn it on before staff pages work. */
  mfa_required?: boolean;
  /** HIPAA approved: data access is written to the activity log. */
  is_healthcare?: boolean;
  /** HIPAA request: null (never asked), "pending" (an admin is checking), "approved", "denied". */
  hipaa_status?: "pending" | "approved" | "denied" | null;
  /** "I'd like to work as a consultant": waiting, approved or denied (null = never asked). */
  consultant_request: "pending" | "approved" | "denied" | null;
  wants_consultant: boolean;
  /** How the consultant helps: "one_time" or "monthly" (null = they do it themselves). */
  consultant_plan: ConsultantPlan | null;
  /** The assigned consultant's first name, e.g. "Brandon". */
  consultant_name: string | null;
}

/** GET /api/consultant/clients — one row in the consultant's "Working on" picker. */
export interface ConsultantClient {
  id: string;
  /** Business name, or the person's name for individuals. */
  label: string;
  person: string;
  email: string;
  account_type: AccountType;
  company_name: string | null;
  location: string | null;
  naics_sector: string | null;
  industry_other: string | null;
  /** One-time analysis or monthly help (null for clients a consultant added by hand). */
  consultant_plan: ConsultantPlan | null;
  /** What they'd like help with (service ids picked on "Your needs"): the picker's "Help with" filter. */
  interested_services: string[];
  /** Last sign-in (ISO), or null: for "Recently active" sorting. */
  last_login_at: string | null;
}

/** GET /api/locations — one city in the location picker. */
export interface Location {
  id: number; // GeoNames id
  label: string; // "Atlanta, Georgia, United States"
  city: string;
  region: string; // US state name ("" elsewhere)
  region_code: string;
  country: string;
  country_code: string;
}

export interface AuthResponse {
  /** null when Supabase must confirm the email first, or the two-step code is needed. */
  access_token: string | null;
  token_type: "bearer";
  /** null while the two-step code is still needed. */
  account: Account | null;
  /** True: no token yet — the person must open the emailed link, then sign in. */
  needs_confirmation?: boolean;
  message?: string | null;
  /** True: the password was right; send mfa_ticket + the authenticator code (signinMfa). */
  mfa_required?: boolean;
  mfa_ticket?: string | null;
  /** Devices signed out to make room (3 at most), e.g. ["Chrome on Windows"]. */
  signed_out_devices?: string[];
}

/** GET /api/accounts/sessions — one signed-in device. Times are ISO. */
export interface DeviceSession {
  id: string;
  /** "Chrome on Windows" */
  device: string;
  signed_in_at: string;
  last_used_at: string;
  /** The device you're using now. */
  this_device: boolean;
}

export interface DeviceSessions {
  sessions: DeviceSession[];
  max_devices: number;
  idle_hours: number;
}

/** POST /api/accounts/mfa/setup — the key to type into an authenticator app. */
export interface MfaSetup {
  secret: string;
  otpauth_url: string;
}

/** GET/POST /api/accounts/hipaa — a business's request to store health information. */
export interface HipaaStatus {
  status: "pending" | "approved" | "denied" | null;
  label: string;
  organization: string | null;
  npi: string | null;
  note: string | null;
  requested_at: string | null;
  decided_at: string | null;
  /** The admin's note (why). */
  decision_note: string | null;
  /** The NPI in the public NPPES registry (for the admin). */
  lookup_url: string | null;
  /** True while waiting: storing data is paused. */
  storing_paused: boolean;
}

/** Admin -> HIPAA requests: one business (waiting first). */
export interface HipaaRequest extends HipaaStatus {
  account_id: string;
  name: string;
  email: string;
  company_name: string | null;
  naics_sector: string | null;
}

/** GET /api/accounts/audit — a healthcare client's activity log (never the data itself). */
export interface AuditEntry {
  at: string;
  action: string;
  actor_name: string | null;
  actor_email: string | null;
  actor_role: string;
  detail: Record<string, string | number | boolean | null>;
}

export interface TeamMemberInput {
  first_name: string;
  last_name: string;
  email: string;
}

export interface SignupInput {
  account_type: AccountType;
  first_name: string;
  last_name: string;
  email: string;
  company_name?: string;
  team_members?: TeamMemberInput[];
  industry_supersector?: Supersector | null;
  naics_sector?: string | null;
  naics_subsector?: string | null;
  company_size?: string | null;
  /** Optional city from the location picker (GeoNames id); signup only. */
  location_id?: number | null;
  /** Services chosen in the questionnaire; put on the new account's plan. */
  service_ids?: string[];
  industry_other?: string | null;
  /** "I'd like a consultant to do this for me." */
  wants_consultant?: boolean;
  /** The consultant picked by name (empty = the server picks one with room). */
  consultant_id?: string | null;
  consultant_plan?: ConsultantPlan;
  /** Who is signing up: a client, or an admin (ADMIN_EMAILS only). Consultants apply after signing up. */
  signup_as?: SignupAs;
  /** The Terms of Service + Privacy Policy box; required for your own sign-up. */
  accept_terms?: boolean;
  /** Required for your own sign-up (8+ characters); left out when a consultant adds a client. */
  password?: string;
}

/** A business team role (api/services/teams.py). */
export type TeamRole = "owner" | "developer" | "member";

/** GET /api/accounts/team — the people on the business team and how full it is. */
export interface TeamInfo {
  team_id: string;
  name: string | null;
  members: Account[];
  /** used = people on the team (owner included); limit = TEAM_MAX_PEOPLE (5). */
  seats: { used: number; limit: number };
  /** role -> label ("Member (view only)"). */
  roles: Record<TeamRole, string>;
  /** The caller is the owner: may add, remove and change roles. */
  can_manage: boolean;
}

/** GET /api/accounts/services — the plan behind the workspace's left pane. */
export interface MyServices {
  /** Service ids the account can use (Data Cleaning is always included). */
  active: string[];
  /** Individuals and team owners can add services; team members can't. */
  can_add: boolean;
  /** False for a view-only team member: they see everything but change nothing. */
  can_edit: boolean;
  team_role: TeamRole | null;
  /** What a first package gets right now (a trial, else the seasonal sale). */
  offer: FirstPackageOffer;
  /** The plan holder's HIPAA request (storing is paused while it's waiting). */
  hipaa: HipaaStatus;
  services: (Service & { active: boolean })[];
  /** Projects they'd like a consultant's help with (interest only; unlocks nothing). */
  interests: string[];
  /** Whose workspace this is — the client when a consultant is working on one. */
  account: Account;
  /** True when a consultant is working on a client's account. */
  acting: boolean;
  /** Live subscriptions, and the plans on sale. */
  subscriptions: LiveSubscription[];
  plans: Subscription[];
  /** stripe=false: test mode — choosing a plan turns it on with no charge. tester: an admin made this account a tester. */
  billing: { stripe: boolean; tester?: boolean };
  /** A live plan includes Data Cleaning: downloads cost nothing extra. */
  downloads_included: boolean;
  /** False until the account has stored data: the AI tool can't be added before that. */
  ai_ready: boolean;
  /** The person looking is an admin: every tool free, nothing to buy. */
  admin: boolean;
  /** Hourly rate and free first call; consulting is booked and paid on Cal.com. */
  consulting: { rate_per_hour_usd: number; free_minutes: number } & ConsultationSummary;
}

// ── Consultations (api/services/consultations.py) ─────────────────────────────

export type ConsultationStatus = "booked" | "canceled" | "ended" | "completed" | "no_show";

/** One free or paid consultation (from the Cal.com webhook, or entered by the consultant). */
export interface Consultation {
  id: string;
  kind: "free" | "paid";
  status: ConsultationStatus;
  source: "calcom" | "manual";
  /** ISO times (UTC). */
  starts_at: string | null;
  ends_at: string | null;
  confirmed_at: string | null;
  note: string | null;
  attendee_email: string | null;
}

/** GET /api/consultations (also inside MyServices.consulting). */
export interface ConsultationSummary {
  /** True once a consultant confirmed the free consultation happened. */
  free_used: boolean;
  /** The link to show now: free until the free call is used, then paid. */
  booking_url: string;
  free_booking_url: string;
  paid_booking_url: string;
  /** False: PAID_BOOKING_URL isn't set, so the free link is reused. */
  paid_link_configured: boolean;
  /** True when CALCOM_WEBHOOK_SECRET is set (bookings arrive automatically). */
  webhook_configured: boolean;
  history: Consultation[];
}

// ── Stored data (Data Engineering; api/routers/warehouse.py) ──────────────────
// Stored in fact.account_data (db/schema.sql): IDs, dates and categories kept
// as they are, every number one "metric" row (type, subtype, unit, value).

export interface PreviewTable {
  columns: string[];
  rows: Record<string, Cell>[];
  total: number;
}

export type ColumnRole = "metric" | "id" | "date" | "category";

/** How one column of the file is stored (api/services/unified.py plan_columns). */
export interface ColumnPlanEntry {
  column: string;
  role: ColumnRole;
  /** The shared (unified) name it is stored under, e.g. "line_amount". */
  field_name: string;
  /** True when the name / description came from the shared field map or from you. */
  mapped: boolean;
  value_type: "text" | "number" | "date" | "timestamp" | "boolean";
  metric_type: string | null;
  metric_subtype: string | null;
  metric_unit: string | null;
  /** How sure the description is: mapped, set by you, high, medium, low (null for non-metrics). */
  confidence: string | null;
  why: string;
}

/** What the client changed in the preview (all optional; the server guesses the rest). */
export interface LoadChoicesBody {
  metric_columns?: string[] | null;
  id_columns?: string[] | null;
  metric_map?: Record<string, { type?: string; subtype?: string | null; unit?: string }> | null;
}

export interface RoundTrip {
  ok: boolean;
  cells_checked: number;
  mismatches: number;
  examples: { row: number; column: string; original: Cell; rebuilt: Cell }[];
}

/** POST /api/warehouse/preview — how the data will be stored, and why. */
export interface LoadPlan {
  source_name: string;
  source_system: string;
  event_type: string;
  event_types: { id: string; label: string; measure: string }[];
  roles: { id: ColumnRole; label: string }[];
  columns: string[];
  column_plan: ColumnPlanEntry[];
  choices: LoadChoicesBody;
  steps: { title: string; why: string }[];
  /** "columns" (how each column is stored) and "account_data" (the first rows as stored). */
  tables: Record<string, PreviewTable>;
  counts: {
    rows_in: number;
    records: number;
    rows_to_load: number;
    metrics: number;
    kept: number;
    needs_review: number;
    duplicates: number;
    blanks: number;
    metric_rows: number;
  };
  /** Rebuilding the table from the rows gives back every cell (nothing is lost). */
  round_trip: RoundTrip;
  /** Send back to /load to accept exactly this preview. */
  fingerprint: string;
  /** "Update my most recent upload": the stored source this load replaces (null = a normal load). */
  replacing?: string | null;
  /** Records already stored from ANOTHER source (same date, amount and reference) — api/services/dedupe.py. */
  duplicates?: { records: number; of: number; record_ids: string[]; sources: string[] };
  /** The same file name is already stored (rows or columns added since): what's new. null = first upload. */
  changes?: StoredChanges | null;
  ready: boolean;
  problem: string | null;
  storage: StorageStatus;
}

export interface StorageStatus {
  /** database = Supabase; memory = local test mode (lost on restart); unavailable = DB down. */
  mode: "database" | "memory" | "unavailable";
  consented: boolean;
  terms_version: string;
}

export interface LoadRun {
  id: string;
  source_name: string;
  event_type: string;
  rows_in: number;
  rows_loaded: number;
  rows_updated: number;
  rows_skipped: number;
  status: "running" | "succeeded" | "failed";
  error: string | null;
  started_at: string;
  finished_at: string | null;
}

/** One stored source (a file, or later a connection such as QuickBooks). */
export interface StoredSource {
  source_system: string;
  source_name: string;
  event_type: string;
  /** Plain English, e.g. "Sales / income (money in)". */
  label: string;
  rows: number;
  /** Rows of the original table (record lines). */
  records: number;
  first_date: string | null;
  last_date: string | null;
}

export interface WarehouseCounts {
  set_up: boolean;
  rows: number;
  records: number;
  sources: StoredSource[];
  first_date: string | null;
  last_date: string | null;
  /** The most recent uploaded FILE still stored ("Update my most recent upload"); null = none. */
  latest_upload: string | null;
}

/** GET /api/warehouse/summary */
export interface WarehouseSummary {
  warehouse: WarehouseCounts;
  loads: LoadRun[];
  storage: StorageStatus;
}

/** POST /api/warehouse/delete — "delete my stored data". */
export interface DeleteStoredResult {
  deleted: { rows: number; sources: number };
  consents_withdrawn: number;
  summary: WarehouseCounts;
}

/** POST /api/warehouse/load */
export interface LoadResult {
  load_run: LoadRun;
  result: {
    rows_loaded: number;
    rows_updated: number;
    rows_already_stored: number;
    /** Records left out because another source already had them ("load only the new records"). */
    records_skipped_as_duplicates?: number;
    /** New customers / vendors / products, and name matches waiting for a person. */
    entities_added?: number;
    matches_to_review?: number;
  };
  summary: WarehouseCounts;
}

// ── Connected data (GET /api/connections, api/routers/connections.py) ────────
export type RefreshFrequency = "manual" | "weekly" | "daily" | "hourly" | "live";

/** One refresh choice (api/services/pricing.py REFRESH_OPTIONS). */
export interface RefreshOption {
  id: RefreshFrequency;
  label: string;
  description: string;
  interval_minutes: number | null;
  refresh_now_gap_minutes: number;
  monthly_usd: number;
  billable: boolean;
  included: boolean;
  default: boolean;
  price_label: string;
}

export interface DataConnection {
  connector: string;
  name: string;
  /** "cloud_storage" = read where it lives (never copied); "business_tool" = copied into storage on a schedule. */
  kind: "cloud_storage" | "business_tool";
  about: string;
  available: boolean;
  stores_data: boolean;
  /** A "Connect" sign-in button is offered (the platform's keys for it are set). */
  sign_in: boolean;
  /** Has the free live quick look (QuickBooks, Xero). */
  live: boolean;
  /** API calls made to it today (UTC). */
  calls_today: number;
  /** The most calls we make to it in a day (Xero 900, QuickBooks 1,000); null = no limit. */
  calls_budget: number | null;
  /** What's connected, e.g. "db.abcd.supabase.co / postgres" or "Sam's Bakery" (never a password or token). */
  display_name: string | null;
  refresh_frequency: RefreshFrequency;
  status: "not_set_up" | "pending" | "connected" | "paused" | "error" | "disconnected";
  last_synced_at: string | null;
  next_sync_at: string | null;
}

/** The free live summary of a connected QuickBooks or Xero (api/services/business_tools.py) — never stored. */
export interface QuickLook {
  source: string; // "QuickBooks" | "Xero"
  company: string;
  start: string; // ISO dates
  end: string;
  /** The earliest date the FREE quick look covers (12 months back); older history = the stored sync. */
  earliest: string;
  /** true = the asked-for start was earlier than `earliest`, so it was moved up. */
  history_limited: boolean;
  currency: string;
  /** true = added up from records (Xero), not the tool's own report. */
  estimate: boolean;
  basis: string;
  /** null = the tool doesn't share it (Wave's API has no expenses or bills). */
  totals: { income: number; expenses: number | null; net_income: number | null; owed_to_you: number; you_owe: number | null };
  monthly: { month: string; income: number; expenses: number | null; net: number | null }[];
  top_customers: { name: string; amount: number }[];
  stored: false;
}

/** POST /api/compare/over-time — two tables by week or month (api/services/compare.py). */
export interface CompareResult {
  /** The periods' first days ("2026-01-01"), both tables together. */
  periods: string[];
  /** null = that table has nothing in that period. */
  left: { label: string; values: (number | null)[] };
  right: { label: string; values: (number | null)[] };
  /** Periods both tables have. */
  overlap: number;
  /** -1 to 1; null when too few shared periods (or one doesn't change). */
  correlation: number | null;
  summary: string;
  cautions: string[];
  period: "week" | "month";
}

/** GET /api/ai/usage — this month's Beacon allowance (api/services/ai_assistant.py). */
export interface AiUsage {
  /** "admin" = no limit; "premium" = the AI package; "free" = everyone else. */
  tier: "admin" | "premium" | "free";
  used: number;
  /** null = no limit (admins). */
  limit: number | null;
  left: number | null;
  /** The 1st of next month (ISO): when the allowance starts over. */
  reset_date: string;
  is_unlimited: boolean;
  /** An admin seeing a tier as a client would ("free" | "premium"). */
  simulating: string | null;
  /** "gemini" = a Google Gemini model plans each question; "built-in" = the plain-English parser. */
  engine: "gemini" | "built-in";
  model: string | null;
  /** A consultant working on a client: whose allowance this is. */
  acting_for: string | null;
}

/** POST /api/ai/ask — one answer. */
export interface AiAnswer {
  answered: boolean;
  result: SkillResult | null;
  /** Why it couldn't be answered (with examples of what works). */
  message: string | null;
  engine: "gemini" | "built-in";
  usage: AiUsage;
  runtime_seconds: number;
}

/** "Is this the same customer?" — a name match between sources waiting for a person (api/services/entities.py). */
export interface EntityMatch {
  link_id: number;
  entity_type: "customer" | "vendor" | "item" | "payee" | "location";
  source_system: string;
  /** The name as the newer source writes it. */
  name: string;
  /** 80-91: close, but not sure. */
  score: number;
  candidate: { entity_id: number; name: string; sources: string[] };
}

/** One run of the stored sync (api/services/sync.py). */
export interface SyncResult {
  connector: string;
  /** Records (invoices, payments...) read from the tool this run. */
  records: number;
  rows_loaded: number;
  rows_updated: number;
  rows_skipped: number;
  /** false = stopped early (the tool's daily allowance); the next sync carries on. */
  complete: boolean;
  /** Records that were ALSO in the client's uploaded files (same date, amount and reference). */
  duplicates_elsewhere: number;
  /** New customers / vendors / products found, and name matches waiting for a person. */
  entities_added: number;
  matches_to_review: number;
  finished_at: string;
}

/** A record type of a connected QuickBooks / Xero that can be opened in the workspace. */
export interface LiveDataset {
  id: string;
  name: string;
  /** false = no records (hidden: nothing to analyze). */
  has_rows: boolean;
}

/** A project in the client's Supabase account ("Sign in with Supabase"). */
export interface SupabaseProject {
  ref: string;
  name: string;
  region: string | null;
}

/** An integration on the roadmap (api/services/connections.py COMING_SOON). */
export interface PlannedIntegration {
  name: string;
  kind: DataConnection["kind"];
  /** "soon" = next in line; "later" = planned after those. */
  when: "soon" | "later";
}

export interface ConnectionsInfo {
  options: RefreshOption[];
  connectors: DataConnection[];
  /** Section titles: {"cloud_storage": "Cloud storage", "business_tool": "Business tools"}. */
  kinds: Record<string, string>;
  /** Planned integrations, shown so clients know their tool is on the way (nothing to set up yet). */
  coming_soon: PlannedIntegration[];
  /** Connected companies per tool, platform-wide, and the most allowed (Xero 5, QuickBooks 15). */
  slots: Record<string, { used: number; max: number }>;
  /** True when the client has Data Engineering. */
  can_connect: boolean;
  addon_total_usd: number;
}

// ── Cloud databases (api/routers/connections.py, api/services/cloud_sources.py) ──

/** One table or view in the client's own database. */
export interface CloudTable {
  schema: string;
  name: string;
  kind: "table" | "view";
  /** PostgreSQL's own estimate (0 = empty or not known yet). */
  rows_estimate: number;
  /** Has at least one row? false = empty (hidden: nothing to analyze); null = couldn't tell. */
  has_rows: boolean | null;
}

/** One schema of the client's database: how many tables, and how many have data. */
export interface CloudSchema {
  schema: string;
  tables: number;
  with_rows: number;
}

/** One cloud table opened as a CSV file (read-only, never stored). */
export interface CloudTableFile {
  file: File;
  /** Rows sent (at most 100,000, and no more than the upload size limit). */
  rows: number;
  /** true = the table has more rows than were opened. */
  truncated: boolean;
}

// ── Dashboards (api/routers/dashboards.py, api/services/dashboards.py) ────────

/** "About your business": what dashboards are built around. */
export interface BusinessContext {
  about: string;
  goals: string;
  key_questions: string[];
}

/** One of the client's own dashboard tabs: saved questions answered on the current data. */
export interface DashboardTab {
  id: string;
  name: string;
  questions: string[];
  position: number;
  updated_at: string | null;
}

/** GET /api/dashboards */
export interface DashboardsInfo {
  tabs: DashboardTab[];
  context: BusinessContext;
  /** The NAICS sector name, e.g. "Retail Trade" (null when not given). */
  industry: string | null;
  /** The NAICS sector code, e.g. "44-45": the Overview's industry KPIs (industry_metrics.json). */
  naics_sector?: string | null;
  company: string | null;
  frequent: { question: string; times: number }[];
  max_tabs: number;
  max_questions: number;
}

/** A question worth a dashboard tab ("yours" = their key question, "asked" = asked often, "industry"). */
export interface DashboardSuggestion {
  question: string;
  why: string;
  source: "yours" | "asked" | "industry";
}


// ── Partners (the home page carousel; api/routers/partners.py) ───────────────

/** One partner in the carousel (GET /api/partners). */
export interface PartnerLogo {
  id: string;
  name: string;
  url: string;          // their website: a click on the logo opens it
  logo_url: string;     // "/api/partners/<id>/logo?v=..." (WebP)
  width: number;
  height: number;
}

export interface PartnerCarouselData {
  partners: PartnerLogo[];
  requirements: string[];
}

export type PartnerChecks = {
  paid_days: number;
  own_domain: boolean;
  website_reachable: boolean;
  email_matches_website: boolean;
  linkedin_given: boolean;
  logo_bytes: number;
  background?: "transparent" | "removed";   // the logo came transparent, or a plain background was taken off
};

/** A business's partner application (GET /api/partners/me). */
export interface PartnerApplication {
  status: "pending" | "approved" | "rejected" | "withdrawn";
  reason: string | null;
  business_name: string;
  website_url: string;
  linkedin_url: string | null;
  logo_url: string;
  logo_width: number;
  logo_height: number;
  checks: PartnerChecks;
  submitted_at: string;
}

export interface PartnerStatus {
  requirements: string[];
  eligible: boolean;
  missing: string[];
  paid_days: number;
  min_paid_days: number;
  application: PartnerApplication | null;
}

/** An application in the admin review list (GET /api/partners/admin/list). */
export interface PartnerReview {
  id: string;
  account_id: string | null;          // null = added by an admin (a company they work with directly)
  source: "platform" | "admin";
  business_name: string;
  website_url: string;
  linkedin_url: string | null;
  status: PartnerApplication["status"];
  checks: PartnerChecks;
  reason: string | null;
  submitted_at: string;
  decided_at: string | null;
  logo_url: string;
  logo_width: number;
  logo_height: number;
}


/** What changed since the stored copy of a file with the same name (warehouse_store.changes_since_stored). */
export interface StoredChanges {
  source: string;
  new_rows: number;
  changed_rows: number;
  unchanged_rows: number;
  /** Stored rows the new file no longer has — they stay stored (Settings → "Update my most recent upload" replaces). */
  rows_not_in_file: number;
  new_columns: string[];
  removed_columns: string[];
}


// ── Enterprise contracts (api/services/enterprise.py) ─────────────────────────

export interface EnterpriseLimits {
  max_upload_mb: number;
  team_seats: number;
  table_rows: number;
}

/** A client's enterprise contract (GET /api/enterprise/me; admins also get notes, raw_limits, ceilings). */
export interface EnterpriseSummary {
  is_enterprise: boolean;
  /** The tag is on and today is within the contract dates. */
  active: boolean;
  price_usd: number | null;
  billing: "monthly" | "yearly" | "one_time" | null;
  setup_fee_usd: number | null;
  contract_start: string | null;
  contract_end: string | null;
  services: string[];
  /** The limits that apply now (raised while the contract is active). */
  limits: EnterpriseLimits;
  defaults: EnterpriseLimits;
  agreement: { version: string; accepted_version: string | null; accepted_at: string | null; accepted_by: string | null;
    needs_acceptance: boolean };
  notes?: string | null;
  raw_limits?: Record<string, number>;   // only the limits the admin raised
  ceilings?: Record<string, number>;
}
