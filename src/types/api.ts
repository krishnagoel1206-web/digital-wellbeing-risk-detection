/**
 * TypeScript mirrors of the backend's response shapes.
 *
 * These are hand-written rather than generated, and deliberately model
 * nullability exactly as the backend does: a field the backend can return as
 * null is typed `| null` here, so the UI is forced to handle the case rather
 * than rendering "null" or crashing.
 *
 * Several fields are optional because the backend omits them in some states
 * (for example `why_no_comparison`, which appears only when a comparison was
 * refused).
 */

export type RiskLevelName = 'Low' | 'Medium' | 'High';

// ---------------------------------------------------------------------------
// Health and status
// ---------------------------------------------------------------------------

export type HealthResponse = {
  status: 'ok' | 'degraded';
  app_name: string;
  python_version: string;
  database: { connected: boolean; error: string | null };
  /** Names of trained artifacts present on disk. Empty when none. */
  models_trained: string[];
  min_days_for_baseline: number;
  /**
   * The backend machine's LAN IPv4 addresses.
   *
   * Advertised because the phone cannot discover the laptop's address by
   * itself. Shown in the app so a changed hotspot IP can be read off the
   * screen rather than hunted down with ipconfig on the laptop.
   */
  lan_addresses: string[];
  port: number;
  /**
   * Which backend answered.
   *
   * 'embedded-python' means it ran inside this app, with no network involved;
   * absent means a development server over HTTP. The distinction matters on
   * screen: an address and a list of LAN addresses are meaningful for one and
   * actively misleading for the other.
   */
  runtime?: 'embedded-python' | string;
};

export type MlStatusResponse = {
  regression: {
    trained: boolean;
    model: string | null;
    trained_at: string | null;
    n_samples: number | null;
    sample_size_warning: string | null;
  };
  classification: {
    trained: boolean;
    model: string | null;
    trained_at: string | null;
    n_samples: number | null;
  };
  how_to_train: string;
};

// ---------------------------------------------------------------------------
// Usage records
// ---------------------------------------------------------------------------

/**
 * Mirrors DataSource in backend/app/database/models.py.
 *
 * 'device' was missing here after the backend enum gained it, so this type
 * quietly disagreed with the API: code could not name the provenance that
 * matters most, and `records_by_source.device` was not even expressible.
 */
export type DataSourceName = 'manual' | 'csv' | 'device' | 'synthetic';

export type UsageRecord = {
  id: number;
  record_date: string;
  total_screen_minutes: number;
  social_minutes: number | null;
  gaming_minutes: number | null;
  productivity_minutes: number | null;
  app_launches: number | null;
  notification_count: number | null;
  unlock_count: number | null;
  calls_count: number | null;
  call_duration_minutes: number | null;
  battery_drain_percent: number | null;
  late_night_minutes: number | null;
  sleep_hours: number | null;
  note: string | null;
  source: DataSourceName;
  created_at: string;
  updated_at: string;
};

export type UsageSummary = {
  total_records: number;
  first_date: string | null;
  last_date: string | null;
  days_of_history: number;
  records_by_source: Partial<Record<DataSourceName, number>>;
  min_days_for_baseline: number;
  has_enough_for_baseline: boolean;
  contains_synthetic_data: boolean;
};

export type UsageRecordInput = {
  record_date: string;
  total_screen_minutes: number;
  social_minutes?: number | null;
  gaming_minutes?: number | null;
  productivity_minutes?: number | null;
  app_launches?: number | null;
  notification_count?: number | null;
  unlock_count?: number | null;
  late_night_minutes?: number | null;
  sleep_hours?: number | null;
  note?: string | null;
};

// ---------------------------------------------------------------------------
// Classification - the Home dashboard headline
// ---------------------------------------------------------------------------

export type RiskClassificationResponse = {
  risk_class: RiskLevelName;
  probabilities: Partial<Record<RiskLevelName, number>>;
  confidence: number;
  model: string;
  days_averaged: number;
  model_inputs: Record<string, number>;
  model_accuracy: {
    cv_accuracy: number | null;
    cv_f1_macro: number | null;
    test_accuracy: number | null;
    roc_auc_ovr: number | null;
    majority_baseline: number | null;
  };
  caveats: (string | null)[];
};

/** Bayesian confidence in the risk class (week-by-week + Dirichlet posterior). */
export type RiskConfidenceResponse = {
  classes: RiskLevelName[];
  n_weeks: number;
  most_likely_class: RiskLevelName;
  verdict: 'very sure' | 'fairly sure' | 'leaning' | 'uncertain';
  plain_language: string;
  prob_most_common: Record<RiskLevelName, number>;
  prob_elevated_majority: number;
  share_mean: Record<RiskLevelName, number>;
  share_interval: Record<RiskLevelName, [number, number]>;
  credible_mass: number;
  prior: string;
  posterior_alpha: Record<RiskLevelName, number>;
  weeks: {
    start: string;
    end: string;
    days: number;
    predicted: RiskLevelName;
    probabilities: Record<RiskLevelName, number>;
    synthetic_days: number;
  }[];
  caveats: (string | null)[];
};

// ---------------------------------------------------------------------------
// Forecasting
// ---------------------------------------------------------------------------

export type ForecastResponse = {
  model: string | null;
  horizon: number;
  predictions: {
    date: string;
    predicted_minutes: number;
    predicted_hours: number;
    /** True once the prediction depends on earlier predictions. */
    is_recursive: boolean;
    /**
     * True when this prediction covers TODAY.
     *
     * The day in progress is excluded from the model's inputs, so the first
     * prediction is for today as a whole. It is deliberately larger than the
     * day-so-far figure the Usage screen shows, and must be labelled rather
     * than placed beside it as though they were the same quantity.
     */
    is_current_day: boolean;
  }[];
  accuracy: {
    rmse_minutes: number | null;
    cv_strategy: string | null;
    naive_baseline_rmse: number | null;
  };
  /** The week ahead against the last complete week. Null with too little history. */
  week_comparison: {
    last_week_avg_minutes: number;
    next_week_avg_minutes: number;
    change_minutes_per_day: number;
    noise_threshold_minutes: number;
    /** True when the change is smaller than the model's error for a 7-day average. */
    within_model_error: boolean;
    summary: string;
  } | null;
  /**
   * What the model was FITTED on, which decides what a prediction means: a
   * model fitted on generated days demonstrates the method, it does not
   * describe this person. Null only for an artifact trained before this was
   * recorded.
   */
  training_data: {
    rows_by_source: Record<string, number>;
    contains_synthetic: boolean | null;
    fitted_on: 'real' | 'mixed' | 'synthetic' | 'unknown';
    note: string;
  } | null;
  /** How far the measured history is from supporting a personally-fitted model. */
  personal_model: {
    measured_days: number;
    days_needed_to_train: number;
    days_needed_for_stable_metrics: number;
    can_train_on_your_data: boolean;
    explanation: string;
  };
  caveats: (string | null)[];
};

export type ActualVsPredictedResponse = {
  series: { date: string; actual_minutes: number; predicted_minutes: number }[];
  n_points: number;
  note: string;
};

// ---------------------------------------------------------------------------
// Risk score (PCA) - a behaviour index, NOT the headline risk
// ---------------------------------------------------------------------------

export type RiskScoreResponse = {
  current_score: number;
  band: RiskLevelName;
  days_scored: number;
  score_history: { date: string; score: number; band: RiskLevelName }[];
  contributions: Record<string, number>;
  elevated_days: number;
  elevated_threshold: number;
  bayesian: {
    estimated_rate: number;
    credible_interval: [number, number];
    credible_mass: number;
    interval_width: number;
    observed: string;
    effective_trials: number;
    autocorrelation: number | null;
    is_reliable: boolean;
    explanation: string;
    notes: string[];
    prior: {
      distribution: string;
      name: string;
      prior_mean: number;
      equivalent_prior_observations: number;
      rationale: string;
    };
    posterior: string;
    next_day: { probability: number; derivation: string; interval_note: string };
  };
  score_method: {
    scale: string;
    bands: string;
    components_retained: number | null;
    validated_against_labels: boolean | null;
    spearman_rho: number | null;
    mean_score_per_class: Record<string, number> | null;
  };
  validation: {
    validated: boolean;
    spearman_rho: number | null;
    mean_score_per_class: Record<string, number> | null;
    monotonic: boolean | null;
    low_to_high_separation: number | null;
  };
  /** False on this dataset - the score did not validate. */
  suitable_as_headline_risk: boolean;
  caveats: string[];
};

// ---------------------------------------------------------------------------
// Clustering
// ---------------------------------------------------------------------------

export type PersonaResponse = {
  cluster_id: number;
  name: string;
  traits: string[];
  population_share: number;
  days_averaged: number;
  model_inputs: Record<string, number>;
  distance_to_assigned: number;
  distance_to_next: number | null;
  assignment_was_close_run: boolean;
  reliability: {
    structure: 'strong' | 'moderate' | 'weak' | 'none' | 'unmeasurable';
    detail: string;
  };
  caveats: (string | null)[];
};

// ---------------------------------------------------------------------------
// Anomalies
// ---------------------------------------------------------------------------

export type AnomalySeverity = 'none' | 'mild' | 'moderate' | 'severe';

export type AnomalyDay = {
  date: string;
  is_anomalous: boolean;
  severity: AnomalySeverity;
  max_abs_z: number;
  /** Unusual as a combination while no single measure is extreme. */
  unusual_combination: boolean;
  /** Multivariate distance from the user's own history; null when it could not be computed. */
  mahalanobis_distance: number | null;
  mahalanobis_p: number | null;
  explanation: string;
  contributing_features: {
    feature: string;
    label: string;
    value: number;
    your_usual: number;
    robust_z: number;
    direction: 'above' | 'below';
    explanation: string;
  }[];
};

export type AnomaliesResponse = {
  days_assessed: number;
  days_flagged: number;
  combination_only_days: number;
  features_checked: string[];
  combination_method: string;
  severity_counts: Partial<Record<AnomalySeverity, number>>;
  baseline_method: string;
  days: AnomalyDay[];
};

// ---------------------------------------------------------------------------
// Recommendations
// ---------------------------------------------------------------------------

export type Recommendation = {
  id: string;
  title: string;
  detail: string;
  action: string;
  evidence_basis: 'population_comparison' | 'personal_baseline';
  claim_type: 'associational' | 'personal_deviation';
  priority: number;
  evidence: Record<string, unknown>;
};

export type RecommendationsResponse = {
  recommendations: Recommendation[];
  n_recommendations: number;
  sources: { population_comparison: number; personal_baseline: number };
  neighbourhood: {
    n_neighbours: number;
    neighbour_risk_distribution: Record<RiskLevelName, number>;
    n_low_risk_neighbours: number;
    mean_distance: number;
    comparison_possible: boolean;
    why_no_comparison?: string;
  };
  claim_policy: { allowed: string; forbidden: string } | null;
  population_grounded_features: string[] | null;
  personal_only_features: string[] | null;
  personal_baseline_available: boolean;
  personal_baseline_note?: string;
  caveats: string[];
};

// ---------------------------------------------------------------------------
// Model performance
// ---------------------------------------------------------------------------

export type ClassificationTestMetrics = {
  accuracy: number;
  precision_macro: number;
  recall_macro: number;
  f1_macro: number;
  precision_weighted: number;
  recall_weighted: number;
  f1_weighted: number;
  roc_auc_ovr: number | null;
  roc_auc_error: string | null;
  confusion: number[][];
  labels: string[];
  majority_baseline: number;
  n_samples: number;
  per_class: Record<string, { precision: number; recall: number; f1: number; support: number }>;
};

export type ModelPerformanceResponse = {
  regression: {
    task: string;
    target: string;
    trained_at: string;
    n_samples: number;
    cv_strategy: string;
    cv_note: string;
    naive_persistence_rmse: number;
    naive_note: string;
    best_model: string;
    sample_size_warning: string | null;
    feature_columns: string[];
    models: {
      name: string;
      rmse: number;
      mae: number;
      r2: number;
      rmse_pct_of_mean: number;
      fold_rmse: number[];
      coefficients: Record<string, number>;
      intercept: number;
      beats_naive: boolean | null;
    }[];
  } | null;
  classification: {
    task: string;
    target: string;
    target_provenance: string;
    trained_at: string;
    n_samples_total: number;
    n_train: number;
    n_test: number;
    classes: string[];
    class_distribution: Record<string, number>;
    majority_baseline: number;
    feature_columns: string[];
    excluded_features: { columns: string[]; reason: string };
    cv_strategy: string;
    selection_rule: string;
    expected_ceiling_note: string;
    best_model: string;
    models: {
      name: string;
      cv_accuracy: number;
      cv_f1_macro: number;
      test: ClassificationTestMetrics;
      feature_importance: Record<string, number>;
    }[];
  } | null;
  note: string;
};

// ---------------------------------------------------------------------------
// Data import
// ---------------------------------------------------------------------------

export type ImportResultResponse = {
  filename: string;
  rows_in_file: number;
  rows_accepted: number;
  rows_rejected: number;
  rows_duplicate: number;
  recognised_columns: Record<string, string>;
  ignored_columns: string[];
  notes: string[];
  problems: { row: number; reason: string }[];
  problems_truncated: boolean;
};

export type SeedDemoResponse = {
  profile: string;
  seed: number;
  days_requested: number;
  synthetic_rows_cleared: number;
  inserted: number;
  skipped_existing_dates: number;
  generator_failures: string[];
  injected_anomalies: { date: string; kind: string; description: string }[];
  notes: string[];
  warning: string;
};

export type FeatureSpecResponse = {
  key: string;
  label: string;
  description: string;
  unit: string;
  origin: 'raw' | 'derived' | 'dataset';
  valid_min: number | null;
  valid_max: number | null;
  used_by: string[];
  formula: string | null;
  caveat: string | null;
};

export type FeatureDictionaryResponse = {
  personal_raw: FeatureSpecResponse[];
  personal_derived: FeatureSpecResponse[];
  population: FeatureSpecResponse[];
  targets: FeatureSpecResponse[];
  classifier_features: string[];
};
