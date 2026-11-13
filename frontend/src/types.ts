export interface TeamRef { id: number; name: string; short_name?: string | null; tla?: string | null; crest?: string | null }
export interface MatchSummary {
  id: number; utc_date: string; status: string; competition?: string | null; competition_code?: string | null;
  matchday?: number | null; home: TeamRef; away: TeamRef; score_home?: number | null; score_away?: number | null;
}
export interface FormSplit { played: number; won: number; drawn: number; lost: number; goals_for: number; goals_against: number }
export interface RecentMatch { date: string; competition?: string | null; opponent: string; venue: "H" | "A"; goals_for: number; goals_against: number; result: "W" | "D" | "L" }
export interface TeamForm {
  team: string; matches_considered: number; overall: FormSplit; home: FormSplit; away: FormSplit; points: number; points_per_game?: number | null;
  avg_goals_for?: number | null; avg_goals_against?: number | null; clean_sheets: number; failed_to_score: number;
  sequence_oldest_to_newest: string; recent_matches: RecentMatch[]; rest_days?: number | null; matches_last_30_days: number;
}
export interface H2H { matches_considered: number; upcoming_home_team_wins: number; draws: number; upcoming_away_team_wins: number; upcoming_home_team_goals: number; upcoming_away_team_goals: number }
export interface StandingRow { position: number; team: string; played: number; won: number; draw: number; lost: number; points: number; goals_for: number; goals_against: number; goal_difference: number }
export interface StandingsCtx { table_size: number; home_team_overall?: StandingRow | null; away_team_overall?: StandingRow | null; home_team_at_home?: StandingRow | null; away_team_away?: StandingRow | null }
export interface LineupPlayer { name: string; position?: string | null; shirt_number?: number | null }
export interface TeamLineup { formation?: string | null; coach?: string | null; starters: LineupPlayer[] }
export interface ViewContext { standings?: StandingsCtx | null; home_form?: TeamForm | null; away_form?: TeamForm | null; head_to_head?: H2H | null; lineups?: { home: TeamLineup; away: TeamLineup } | null }
export interface Step { key: string; label: string; status: "pending" | "running" | "done" | "error"; message?: string | null }

export type Reliability = "high" | "medium" | "low" | "unknown";
export interface StatFinding { statement: string; source_id?: string | null; reliability: Reliability }
export interface TeamResearch {
  name: string; league_position?: number | null; recent_form: string; home_away_performance: string; rest_and_congestion: string;
  player_availability: { status: string; injuries: string[]; suspensions: string[]; confirmed_lineup?: string | null; notes: string };
}
export interface ResearcherReport {
  home_team: TeamResearch; away_team: TeamResearch; head_to_head: string; key_findings: StatFinding[]; advanced_stats_status: string;
  data_quality: { level: string; missing_data: string[]; notes: string }; uncertainties: string[];
}

export type ClaimType = "confirmed_fact" | "credible_report" | "opinion" | "rumour" | "unverified";
export interface NewsFinding {
  item_id: string; team: "home" | "away" | "both"; category: string; claim_type: ClaimType; summary: string;
  exact_quote?: string | null; event_date?: string | null; reliability_score: number; relevance: string; uncertainty: "low" | "medium" | "high";
  source?: string | null; link?: string | null; published_at?: string | null; source_reliability_class?: string | null; quote_verified?: boolean | null;
}
export interface TeamIntel { team: string; summary: string; morale_and_pressure: string; availability_news: string[]; findings: NewsFinding[] }
export interface IntelligenceReport {
  status: "ok" | "partial" | "unavailable"; home: TeamIntel; away: TeamIntel; cross_cutting: string[]; overall_relevance: string; caveats: string[];
}

export interface Scoreline { score: string; count: number; pct: number }
export interface ScenarioResult {
  name: string; description: string; assigned_probability_pct: number; simulated_n: number; assumptions: string[]; impact_on_teams: string;
  evidence: string; home_log_effect: number; away_log_effect: number; uncertainty_sd: number; flags: string[];
  home_win_pct: number; draw_pct: number; away_win_pct: number; avg_home_goals: number; avg_away_goals: number;
  btts_pct: number; over_2_5_pct: number; top_scorelines: Scoreline[];
}
export interface ParamStat { mean: number; sd: number; p05: number; p50: number; p95: number }
export interface AppliedAdjustment {
  target: string; requested_log_effect: number; applied_log_effect: number; evidence: string; status: string; reason: string; justification: string;
}
export interface Simulation {
  n_simulations: number; model: string; seed: number; config: Record<string, any>; priors: Record<string, any>;
  applied_adjustments: AppliedAdjustment[]; parameter_uncertainty: Record<string, ParamStat>; warnings: string[];
  home_win_pct: number; draw_pct: number; away_win_pct: number; avg_home_goals: number; avg_away_goals: number; avg_total_goals: number;
  input_xg_home: number; input_xg_away: number; home_scores_pct: number; away_scores_pct: number; btts_pct: number;
  over_1_5_pct: number; over_2_5_pct: number; over_3_5_pct: number; under_2_5_pct: number;
  home_clean_sheet_pct: number; away_clean_sheet_pct: number; top_scorelines: Scoreline[];
  score_matrix_pct: number[][]; goal_distribution: { home: number[]; away: number[]; total: number[] };
  scenarios: ScenarioResult[]; events: Record<string, any>;
  uncertainty: { monte_carlo_standard_error_pct: Record<string, number>; xg_home_90pct_interval: number[]; xg_away_90pct_interval: number[]; note: string };
  disclaimer: string;
}
export interface Prediction {
  predicted_score: { home: number; away: number };
  probabilities: { home_win: number; draw: number; away_win: number };
  over_under_2_5: { over_2_5: number; under_2_5: number };
  both_teams_to_score: { yes: number; no: number };
  most_likely_scorelines: string[]; explanation: string; simulation_interpretation: string; scenario_commentary: string; parameter_commentary: string;
  accepted_conclusions: string[]; rejected_conclusions: string[]; contradictions_found: string[]; unsupported_claims: string[];
  confidence: { level: string; score: number; explanation: string; main_uncertainties: string[] };
}
export interface ChiefPlan {
  report_assessment: { contradictions: string[]; unsupported_claims: string[]; accepted_conclusions: string[]; rejected_conclusions: string[]; missing_information: string[]; data_quality_notes: string };
  abstain: boolean; abstain_reason: string; parameter_notes: string;
}
export interface DataIssue { kind: string; severity: "info" | "warning" | "critical"; detail: string }
export interface DataQuality { score: number; level: string; issues: DataIssue[]; abstain_recommended: boolean; components: Record<string, boolean> }
export interface SourceRecord { id: string; name: string; url?: string | null; retrieved_at: string; published_at?: string | null; reliability: Reliability; freshness: string; status: string; note?: string | null }
export interface AnalysisResult {
  match: MatchSummary; researcher: ResearcherReport | null; intelligence: IntelligenceReport | null; chief_plan: ChiefPlan | null;
  simulation: Simulation | null; prediction: Prediction | null; abstained: boolean; abstain_reason: string;
  data_quality: DataQuality | null; context?: ViewContext | null; sources: SourceRecord[]; data_availability: Record<string, string>; warnings: string[];
  models: Record<string, string>; prediction_record: { status?: string; created_at?: string; note?: string; reason?: string }; generated_at: string;
}
export interface Job {
  job_id: string; status: "running" | "done" | "error"; steps: Step[]; result: AnalysisResult | null;
  error: { code: string; message: string; agent?: string | null } | null;
}
export interface Health {
  status: string; football_api_configured: boolean; warnings: string[];
  agents: { name: string; key_configured: boolean; model: string }[];
}
export interface Market { accuracy?: number; brier?: number; log_loss?: number; n?: number }
export interface EvalReport {
  n: number; sample_warning?: string | null; outcome_1x2?: Market; home_team_scores?: Market; away_team_scores?: Market; btts?: Market;
  over_2_5?: Market; goals_mae?: { home: number; away: number; total: number }; exact_score?: { accuracy: number; mean_probability_of_actual_score?: number | null };
  calibration?: { bin_low: number; bin_high: number; mean_predicted: number; observed_frequency: number; n: number }[];
  roi?: { available: boolean; reason?: string; roi?: number };
}
export interface Evaluation {
  stored_predictions: number; evaluated: number; tampered_match_ids: number[];
  chief_predictor: EvalReport; simulation_only: EvalReport; baseline_constant_frequencies: EvalReport | null; storage_note: string;
}
