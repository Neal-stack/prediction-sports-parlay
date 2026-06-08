const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type RiskLevel = "safe" | "balanced" | "bold";

export type PickLeg = {
  game_id: string;
  sport: string;
  matchup: string;
  market: "moneyline" | "spread" | "total";
  selection: string;
  odds_american: number;
  implied_prob: number;
  win_probability: number;
  confidence: number;
  score: number;
  rationale: string;
  user_probability?: number | null;
  edge_vs_implied?: number | null;
};

export type ParlayResponse = {
  legs: PickLeg[];
  combined_american: number;
  combined_implied_prob: number;
  estimated_win_prob: number;
  payout_on_100: number;
  risk: RiskLevel;
  same_game: boolean;
  summary: string;
  ai_insight?: string | null;
  generated_at: string;
};

export type EdgeAnalysisResponse = {
  legs: PickLeg[];
  user_estimated_win_prob: number;
  model_estimated_win_prob: number;
  avg_edge_vs_implied: number;
  summary: string;
};

export type LineMovementPoint = {
  captured_at: string;
  moneyline_home?: number | null;
  moneyline_away?: number | null;
  spread_home?: number | null;
  total?: number | null;
};

export type SavedBet = {
  id: string;
  stake: number;
  combined_american: number;
  estimated_win_prob: number;
  legs: number;
  risk: RiskLevel;
  outcome: "pending" | "win" | "loss" | "push";
  saved_at: string;
  potential_payout: number;
};

export type StatusResponse = {
  demo_mode: boolean;
  sharpapi: boolean;
  supabase: boolean;
  api_sports: boolean;
  gnews: boolean;
  weather: string;
  ai_provider?: string | null;
  games_cached: number;
};

export type GameSummary = {
  id: string;
  sport: string;
  home_team: string;
  away_team: string;
  start_time: string;
  venue?: string | null;
  is_outdoor?: boolean;
  moneyline_home?: number | null;
  moneyline_away?: number | null;
  spread_home?: number | null;
  spread_home_odds?: number;
  total?: number | null;
  over_odds?: number;
};

export async function fetchGames(sport?: string): Promise<GameSummary[]> {
  const q = sport ? `?sport=${encodeURIComponent(sport)}` : "";
  const res = await fetch(`${API_BASE}/api/games${q}`);
  if (!res.ok) throw new Error("Failed to load games");
  return res.json();
}

export async function fetchLineMovement(
  gameId: string
): Promise<LineMovementPoint[]> {
  const res = await fetch(`${API_BASE}/api/games/${encodeURIComponent(gameId)}/line-movement`);
  if (!res.ok) throw new Error("No line history yet");
  return res.json();
}

export async function fetchStatus(): Promise<StatusResponse> {
  const res = await fetch(`${API_BASE}/api/status`);
  if (!res.ok) throw new Error("Failed to load status");
  return res.json();
}

export async function generateParlay(opts?: {
  legs?: number;
  sport?: string | null;
  risk?: RiskLevel;
  game_id?: string | null;
}): Promise<ParlayResponse> {
  const res = await fetch(`${API_BASE}/api/parlay/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      legs: opts?.legs ?? 3,
      sport: opts?.sport ?? null,
      risk: opts?.risk ?? "balanced",
      game_id: opts?.game_id ?? null,
    }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail ?? "Failed to generate parlay");
  }
  return res.json();
}

export async function analyzeEdge(
  parlay: ParlayResponse,
  userEdges: { leg_index: number; user_probability: number }[]
): Promise<EdgeAnalysisResponse> {
  const res = await fetch(`${API_BASE}/api/parlay/analyze-edge`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ parlay, user_edges: userEdges }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail ?? "Edge analysis failed");
  }
  return res.json();
}

export async function sendChat(
  message: string,
  parlay?: ParlayResponse | null
): Promise<string> {
  const res = await fetch(`${API_BASE}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, parlay: parlay ?? null }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail ?? "Chat failed");
  }
  const data = await res.json();
  return data.reply;
}

export function formatAmerican(odds: number): string {
  return odds > 0 ? `+${odds}` : `${odds}`;
}

export function formatPercent(prob: number): string {
  return `${(prob * 100).toFixed(1)}%`;
}

export const RISK_OPTIONS: {
  value: RiskLevel;
  label: string;
  hint: string;
}[] = [
  {
    value: "safe",
    label: "Safe",
    hint: "Higher win chance, modest payout (+180–+400)",
  },
  {
    value: "balanced",
    label: "Balanced",
    hint: "Best edge-to-payout ratio (+350–+800)",
  },
  {
    value: "bold",
    label: "Bold",
    hint: "Bigger payout, still signal-driven (+650+)",
  },
];
