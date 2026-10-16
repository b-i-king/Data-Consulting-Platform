// ── API response types ────────────────────────────────────────────────────────
// These TypeScript types mirror the JSON the FastAPI backend returns.
// If you change a response in `api/`, update the matching type here so the
// editor and `npm run typecheck` catch mismatches.

export interface Choice {
  id: string;
  label: string;
  description?: string;
  available?: boolean;
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
}

/** The Admin panel: the requests, plus every consultant's load. */
export interface AdminPanelData {
  requests: ConsultantRequest[];
  consultants: ConsultantLoad[];
}

/**
 * One priced item = a Stripe product with a monthly and an annual price: a TOOL
 * (build your own package; the total is the sum) or the All tools BUNDLE.
 */
export interface Subscription {
  id: string; // "data_cleaning", "data_visualization", ..., "ai", "all_tools"
  kind: "tool" | "bundle";
  /** Data Cleaning: in every package. */
  base: boolean;
  /** Tools that must be picked too (AI needs "data_engineering"). */
  requires: string[];
  /** false = free until it launches (AI). */
  billable: boolean;
  /** Bundle only: how much less per month than picking every tool. */
  bundle_savings_month_usd: number;
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
  breakdown: Record<string, { category: string; current: number; previous: number | null; pct_change: number | null }[]>;
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
  team_role: string | null;
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
  /** null when Supabase must confirm the email first (needs_confirmation). */
  access_token: string | null;
  token_type: "bearer";
  account: Account;
  /** True: no token yet — the person must open the emailed link, then sign in. */
  needs_confirmation?: boolean;
  message?: string | null;
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
  /** "I'd like to work as a consultant" — waits for an admin's approval. */
  consultant_request?: boolean;
  /** The Terms of Service + Privacy Policy box; required for your own sign-up. */
  accept_terms?: boolean;
  /** Required for your own sign-up (8+ characters); left out when a consultant adds a client. */
  password?: string;
}

/** GET /api/accounts/services — the plan behind the workspace's left pane. */
export interface MyServices {
  /** Service ids the account can use (Data Cleaning is always included). */
  active: string[];
  /** Individuals and team owners can add services; team members can't. */
  can_add: boolean;
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
  /** stripe=false: test mode — choosing a plan turns it on with no charge. */
  billing: { stripe: boolean };
  /** A live plan includes Data Cleaning: downloads cost nothing extra. */
  downloads_included: boolean;
  /** False until the account has stored data: the AI tool can't be added before that. */
  ai_ready: boolean;
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
  result: { rows_loaded: number; rows_updated: number; rows_already_stored: number };
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
  available: boolean;
  refresh_frequency: RefreshFrequency;
  status: "not_set_up" | "pending" | "connected" | "paused" | "error" | "disconnected";
  last_synced_at: string | null;
  next_sync_at: string | null;
}

export interface ConnectionsInfo {
  options: RefreshOption[];
  connectors: DataConnection[];
  /** True when the client has Data Engineering. */
  can_connect: boolean;
  addon_total_usd: number;
}
