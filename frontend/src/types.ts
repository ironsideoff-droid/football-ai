export interface TeamRef { id: number; name: string; short_name?: string | null; tla?: string | null; crest?: string | null }
export interface MatchSummary {
  id: number; utc_date: string; status: string; competition?: string | null; competition_code?: string | null;
  matchday?: number | null; home: TeamRef; away: TeamRef; score_home?: number | null; score_away?: number | null;
}
export interface Step { key: string; label: string; status: "pending" | "running" | "done" | "error"; message?: string | null }

export interface ResearcherReport {
  home_team: TeamResearch; away_team: TeamResearch; head_to_head: string; key_findings: string[];
  data_quality: { level: string; missing_data: string[]; notes: string };
  sources: { name: string; timestamp?: string | null }[]; uncertainties: string[];
}
export interface TeamResearch {
  name: string; league_position?: number | null; recent_form: string; home_away_performance: string;
  player_availability: { status: string; injuries: string[]; suspensions: string[]; notes: string };
}
export interface TacticalProfile { style: string; formation?: string | null; strengths: string[]; weaknesses: string[]; basis: string }
export interface TacticalReport {
  home_profile: TacticalProfile; away_profile: TacticalProfile; offensive_analysis: string; defensive_analysis: string;
  key_matchups: string[]; tactical_advantages: string[]; scenarios: { name: string; description: string; likelihood?: string | null }[];
  expected_match_dynamics: string; scoring_expectations: { home_goals: number; away_goals: number; reasoning: string };
  confidence: { level: string; uncertainties: string[] };
}
export interface SimParams { home_xg: number; away_xg: number; uncertainty_sigma: number; goal_correlation: number; rationale: string; key_inputs: string[] }
export interface Simulation {
  n_simulations: number; model: string; seed: number; params_requested: SimParams; params_applied: SimParams; param_warnings: string[];
  home_win_pct: number; draw_pct: number; away_win_pct: number; avg_home_goals: number; avg_away_goals: number; avg_total_goals: number;
  over_1_5_pct: number; over_2_5_pct: number; over_3_5_pct: number; under_2_5_pct: number; btts_pct: number;
  home_clean_sheet_pct: number; away_clean_sheet_pct: number;
  top_scorelines: { score: string; count: number; pct: number }[];
  goal_distribution: { home: number[]; away: number[]; total: number[] }; disclaimer: string;
}
export interface Prediction {
  predicted_score: { home: number; away: number };
  probabilities: { home_win: number; draw: number; away_win: number };
  over_under_2_5: { over_2_5: number; under_2_5: number };
  both_teams_to_score: { yes: number; no: number };
  most_likely_scorelines: string[]; explanation: string; simulation_interpretation: string;
  accepted_conclusions: string[]; rejected_conclusions: string[]; contradictions_found: string[]; unsupported_claims: string[];
  confidence: { level: string; score: number; explanation: string; main_uncertainties: string[] };
}
export interface AnalysisResult {
  match: MatchSummary; researcher: ResearcherReport; tactical: TacticalReport; simulation: Simulation; prediction: Prediction;
  chief_plan: { report_assessment: { contradictions: string[]; unsupported_claims: string[]; accepted_conclusions: string[]; rejected_conclusions: string[]; data_quality_notes: string } };
  data_availability: Record<string, string>; warnings: string[]; models: Record<string, string>; generated_at: string;
}
export interface Job {
  job_id: string; status: "running" | "done" | "error"; steps: Step[]; result: AnalysisResult | null;
  error: { code: string; message: string; agent?: string | null } | null;
}
export interface Health {
  status: string; football_api_configured: boolean; warnings: string[];
  agents: { name: string; key_configured: boolean; model: string }[];
}
