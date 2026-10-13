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
  pricing_model: "runtime" | "package" | "consultation" | "early_access";
  starting_price_usd: number;
  automated: boolean;
  requires_consultant: boolean;
  includes: string[];
  /** false = not sold yet; shown with an "Early access" badge. */
  available: boolean;
  /** Client-friendly names of the skills behind this service (data_skills/catalog.json). */
  capabilities: string[];
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

/** Business industry answers (all optional; each narrows the previous). */
export interface Industry {
  industry_supersector: Supersector | null;
  naics_sector: string | null;
  naics_subsector: string | null;
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
  limits: { max_upload_mb: number; max_files_per_upload: number };
  pricing: { price_per_runtime_minute_usd: number; billing_increment_seconds: number };
  booking_url: string;
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

export interface GroupOutput {
  group_id: number;
  label: string;
  report: CleaningReport;
  sample_size: SampleSize;
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

export interface SkillResult {
  skill: string;
  summary: string;
  findings: string[];
  tables: Record<string, Record<string, Cell>[]>;
  charts: (LineChartSpec | BarChartSpec)[];
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
  metrics: string[];
  metric_labels: Record<string, string>;
  dimension_column: string | null;
  as_of: string;
  first_date: string;
  default_range: string;
  default_comparison: string;
  range_labels: Record<string, string>;
  comparison_labels: Record<string, string>;
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
}

export interface AuthResponse {
  access_token: string;
  token_type: "bearer";
  account: Account;
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
}
