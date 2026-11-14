// Shape of artifacts/dashboard.json, written by demand_agent.export.

export interface Band {
  mean: number[];
  p10: number[];
  p90: number[];
}

export interface Store {
  id: string;
  name: string;
  lat: number;
  lon: number;
  weeksOpen: number;
  demand28: number;
  sales28: number;
  forecast28: number;
  revenue28: number;
  stockUnits: number;
  stockValue: number;
  riskCells: number;
  history: number[];
  forecast: Band;
  lqCategory: number[];
  lqStyle: number[];
}

export interface Category {
  key: string;
  label: string;
  skus: number;
  demand28: number;
  forecast28: number;
  history: number[];
  forecast: Band;
}

export interface Sku {
  id: string;
  name: string;
  category: string;
  subtype: string;
  style: string;
  color: string;
  fit: string;
  label: string;
  price: number;
  weeksLive: number;
  demand28: number;
  forecast28: number;
  trend: string;
}

export interface Pair {
  s: number;
  k: number;
  level: number;
  age: number;
  abc: string;
  sales: number[];
  demand: number[];
  mean: number[];
  p10: number[];
  p25: number[];
  p50: number[];
  p75: number[];
  p90: number[];
  cumMean: number[];
  cumP90: number[];
  drivers: number[];
  stock: number[];
  inbound: number[];
  share: number[];
  exp14: number[];
  risk14: number[];
  riskWeek: number[];
  riskAfter: number[];
  order: number[];
  cover: number | null;
}

export interface Risk {
  store: string;
  sku_id: string;
  product: string;
  max_size_risk: number;
  expected_lost_units: number;
  expected_lost_revenue: number;
  expected_demand: number;
  on_hand: number;
  sizes_out: string[];
  s: number;
  k: number;
}

export interface Overstock {
  store: string;
  sku_id: string;
  product: string;
  on_hand: number;
  weeks_of_cover: number;
  stock_value: number;
  suggestion: string;
  s: number;
  k: number;
}

export interface OrderLine {
  store: string;
  sku_id: string;
  product: string;
  category: string;
  class: string;
  sizes: Record<string, number>;
  units: number;
  cost: number;
  on_hand: number;
  expected_14d: number;
  risk_before: number;
  risk_after: number;
  reason: string;
  approval: "auto" | "review";
  review_reason: string | null;
}

export interface TransferMove {
  from: string;
  to: string;
  sku_id: string;
  product: string;
  size: string;
  units: number;
  donor_keeps: number;
  receiver_risk_before: number;
}

export interface Lane {
  from: string;
  to: string;
  units: number;
  km: number;
  cost: number;
}

export interface Guardrail {
  check: string;
  status: "pass" | "warn" | "fail";
  detail: string;
}

export interface PlanSummary {
  service_levels: Record<string, number>;
  transfers: {
    units: number;
    lanes: number;
    cost: number;
    expected_margin_recovered: number;
    solver: string;
    solve_seconds: number;
  };
  supplier_orders: {
    units: number;
    lines: number;
    cost: number;
    cost_without_budget: number;
    budget: number | null;
    budget_binding: boolean;
  };
  expected_impact: {
    lost_units_this_week: [number, number];
    lost_units_14d: [number, number];
    revenue_protected: number;
    cells_above_50pct_risk: [number, number];
  };
}

export interface Plan {
  orders: OrderLine[];
  transfers: TransferMove[];
  lanes: Lane[];
  summary: PlanSummary | null;
  guardrails: Guardrail[];
}

export interface Trend {
  attribute: string;
  value: string;
  status: "Emerging" | "Rising" | "Peaking" | "Fading" | "Stable";
  slope_pct_per_week: number;
  change_4w: number;
  p_value: number;
  q_value: number;
  share: number;
  series: number[];
  buzz_momentum: number | null;
  buzz_series: number[] | null;
  leading_stores: string[];
  top_skus: string[];
}

export interface Anomaly {
  scope: "city" | "store" | "product";
  name: string;
  entity: number;
  start: string;
  end: string;
  day: number;
  days: number;
  direction: "spike" | "drop";
  z: number;
  actual: number;
  expected: number;
  cause: string | null;
}

export interface PolicyResult {
  name: string;
  kind: "baseline" | "agent" | "agent_orders" | "agent_no_transfers";
  dial: string;
  fill_rate: number;
  demand_units: number;
  sold_units: number;
  lost_units: number;
  revenue: number;
  lost_revenue: number;
  gross_margin: number;
  avg_stock_units: number;
  avg_stock_value: number;
  end_stock_value: number;
  stockout_share: number;
  units_ordered: number;
  transfer_units: number;
  transfer_trips: number;
  transfer_cost: number;
  holding_cost: number;
  net_contribution: number;
  daily_fill: number[];
  daily_stock: number[];
}

export type MethodTable = Record<string, { wape: number; bias: number }>;

export interface ForecastEval {
  methods: string[];
  overall: MethodTable;
  noise_floor: number;
  skill_vs_trailing: number;
  skill_vs_in_stock: number;
  by_window: Record<string, Record<string, number>>;
  by_category: Record<string, Record<string, number>>;
  by_store: Record<string, Record<string, number>>;
  by_age: Record<string, Record<string, number>>;
  by_fold: Record<string, MethodTable>;
  levels: Record<string, Record<string, Record<string, number>>>;
  coverage: { nominal: number[]; raw: number[]; calibrated: number[]; reference: number[] };
  interval80: { nominal: number; raw: number; calibrated: number };
  p90_by_length: Record<string, { raw: number; calibrated: number }>;
  pinball: Record<string, number>;
  rows: number;
  calibrated_rows: number;
  folds: { origin: string; calibrated: boolean; train_rows: number; live: boolean }[];
}

export interface Evaluation {
  forecast: ForecastEval;
  uncensoring: {
    censored_share: number;
    true_lost_units: number;
    recovered_units: number;
    recovery_ratio: number;
    bias_sales: number;
    bias_uncensored: number;
    bias_sales_last_28d: number;
    bias_uncensored_last_28d: number;
    weekly_true: number[];
    weekly_sales: number[];
    weekly_estimate: number[];
  };
  size_curves: {
    buckets: { bucket: string; pairs: number; raw_mae: number; pooled_mae: number }[];
    concentration: Record<string, number>;
  };
  trends: {
    style: string;
    status: string;
    detected_change_4w: number;
    slope_pct_per_week: number;
    q_value: number;
    buzz_momentum: number | null;
    true_change_8w: number;
    true_change_next_4w: number;
  }[];
  anomalies: Record<
    string,
    { injected: number; detected: number; recall: number | null; unexplained_flags: number; precision: number | null }
  >;
  policy: {
    horizon_days: number;
    weekly_budget: number;
    results: PolicyResult[];
    headline: {
      status_quo: PolicyResult;
      agent: PolicyResult;
      agent_orders_only: PolicyResult;
      lost_revenue_cut: number;
      fill_rate_gain: number;
      stock_value_change: number;
      net_contribution_gain: number;
      transfer_contribution: number;
      same_service_baseline: PolicyResult;
      same_service_reached: boolean;
    };
    weekly_orders: { cost: number; unconstrained: number; transfer_units: number; solver: string }[];
  } | null;
  aggregateSd: Record<string, number>;
}

export interface AgentStep {
  n: number;
  thought: string;
  tool: string;
  args: Record<string, unknown>;
  observation: string;
  seconds: number;
}

export interface Finding {
  kind: "demand" | "risk" | "trend" | "anomaly" | "plan" | "watch";
  title: string;
  body: string;
}

export interface Agent {
  mode: "rules" | "llm";
  model: string | null;
  status: string;
  seconds: number;
  brief: { headline: string; findings: Finding[]; stats: Record<string, number>; narrative: string | null };
  steps: AgentStep[];
  tools: { name: string; description: string }[];
}

export interface Dashboard {
  meta: {
    asOf: string;
    horizonDays: number;
    seed: number;
    counts: { stores: number; skus: number; sizes: number; days: number; pairs: number; salesRows: number };
    model: {
      features: number;
      trainRows: number;
      quantiles: number[];
      importance: Record<string, number>;
      driverNames: string[];
      offsets: Record<string, number[]>;
    };
    timings: Record<string, number>;
    policy: {
      serviceLevels: Record<string, number>;
      budget: number;
      approvalThreshold: number;
      leadTimeDays: number;
      protectionDays: number;
    };
    sizes: string[];
    styles: string[];
    categories: { key: string; label: string }[];
  };
  network: {
    weeks: string[];
    sales: number[];
    demand: number[];
    forecast: Band;
    events: { week: number; name: string }[];
  };
  categories: Category[];
  stores: Store[];
  skus: Sku[];
  pairs: Pair[];
  risks: Risk[];
  riskWeek: { cells: number; lostRevenue: number; pairs: number };
  overstock: Overstock[];
  plan: Plan;
  trends: Trend[];
  anomalies: Anomaly[];
  anomalySeries: { dates: string[]; actual: number[]; expected: number[]; rain: number[]; events: string[] };
  seasonality: { weeks: string[]; index: Record<string, number[]>; weekday: number[][] };
  evaluation: Evaluation;
  agent: Agent;
}
