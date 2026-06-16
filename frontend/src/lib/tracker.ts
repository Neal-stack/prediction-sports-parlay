import type {
  LegOutcome,
  ParlayResponse,
  PerformanceStats,
  PickLeg,
  RiskLevel,
  SavedParlayRecord,
} from "./api";
import { getSessionId } from "./session";

const STORAGE_KEY = "parlay-tracker-v2";
const MAX_SAVED = 30;

function legDecimal(oddsAmerican: number): number {
  return oddsAmerican > 0 ? 1 + oddsAmerican / 100 : 1 + 100 / Math.abs(oddsAmerican);
}

/** Append a leg (e.g. a prop anchor) and recompute combined odds + win prob,
 *  mirroring the backend's combine_american_odds. */
export function addLegToParlay(parlay: ParlayResponse, leg: PickLeg): ParlayResponse {
  const legs = [...parlay.legs, leg];
  let decimal = 1;
  let win = 1;
  for (const l of legs) {
    decimal *= legDecimal(l.odds_american);
    win *= l.win_probability;
  }
  const american =
    decimal >= 2 ? Math.round((decimal - 1) * 100) : Math.round(-100 / (decimal - 1));
  const payout = american > 0 ? american : 100 * (100 / Math.abs(american));
  return {
    ...parlay,
    legs,
    combined_american: american,
    combined_implied_prob: Math.round((1 / decimal) * 10000) / 10000,
    estimated_win_prob: Math.round(win * 10000) / 10000,
    payout_on_100: Math.round(payout * 100) / 100,
    anchors: (parlay.anchors ?? []).filter(
      (a) => !(a.player === leg.player && a.stat === leg.stat && a.selection === leg.selection)
    ),
  };
}

export function potentialPayout(stake: number, combinedAmerican: number): number {
  if (combinedAmerican > 0) {
    return stake * (combinedAmerican / 100);
  }
  return stake * (100 / Math.abs(combinedAmerican));
}

export function computeParlayOutcome(legOutcomes: LegOutcome[]): LegOutcome {
  if (!legOutcomes.length || legOutcomes.some((o) => o === "pending")) return "pending";
  if (legOutcomes.some((o) => o === "loss")) return "loss";
  if (legOutcomes.every((o) => o === "win")) return "win";
  return "push";
}

type TrackerState = {
  bankroll: number;
  parlays: SavedParlayRecord[];
};

function defaultState(): TrackerState {
  return { bankroll: 500, parlays: [] };
}

function isValidState(value: unknown): value is TrackerState {
  if (!value || typeof value !== "object") return false;
  const v = value as TrackerState;
  return typeof v.bankroll === "number" && Array.isArray(v.parlays);
}

export function loadTrackerState(): TrackerState {
  if (typeof window === "undefined") return defaultState();
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return defaultState();
    const parsed: unknown = JSON.parse(raw);
    return isValidState(parsed) ? parsed : defaultState();
  } catch {
    return defaultState();
  }
}

export function saveTrackerState(state: TrackerState): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
}

export function createLocalParlay(
  parlay: ParlayResponse,
  stake: number
): SavedParlayRecord {
  const legOutcomes: LegOutcome[] = parlay.legs.map(() => "pending");
  return {
    id: crypto.randomUUID(),
    session_id: getSessionId(),
    stake,
    combined_american: parlay.combined_american,
    combined_implied_prob: parlay.combined_implied_prob,
    estimated_win_prob: parlay.estimated_win_prob,
    risk: parlay.risk,
    same_game: parlay.same_game,
    outcome: "pending",
    legs: parlay.legs.map((l) => ({ ...l })),
    leg_outcomes: legOutcomes,
    summary: parlay.summary,
    generated_at: parlay.generated_at,
    saved_at: new Date().toISOString(),
  };
}

export function updateLegOutcomeLocal(
  state: TrackerState,
  parlayId: string,
  legIndex: number,
  outcome: Exclude<LegOutcome, "pending">
): TrackerState {
  const parlays = state.parlays.map((p) => {
    if (p.id !== parlayId) return p;
    const leg_outcomes = [...p.leg_outcomes];
    leg_outcomes[legIndex] = outcome;
    const parlayOutcome = computeParlayOutcome(leg_outcomes);
    return {
      ...p,
      leg_outcomes,
      outcome: parlayOutcome,
      settled_at:
        parlayOutcome !== "pending" ? new Date().toISOString() : p.settled_at,
    };
  });
  return { ...state, parlays };
}

export function settleBankroll(
  state: TrackerState,
  parlay: SavedParlayRecord,
  previousOutcome: LegOutcome
): TrackerState {
  if (parlay.outcome === previousOutcome) return state;
  let bankroll = state.bankroll;
  const payout = potentialPayout(parlay.stake, parlay.combined_american);

  if (previousOutcome !== "pending") {
    if (previousOutcome === "win") bankroll -= payout;
    if (previousOutcome === "loss") bankroll += parlay.stake;
  }

  if (parlay.outcome === "win") bankroll += payout;
  if (parlay.outcome === "loss") bankroll -= parlay.stake;

  return { bankroll: Math.max(0, bankroll), parlays: state.parlays };
}

export function localPerformance(parlays: SavedParlayRecord[]): PerformanceStats {
  const settled = parlays.filter((p) => p.outcome !== "pending");
  const wins = settled.filter((p) => p.outcome === "win").length;
  const losses = settled.filter((p) => p.outcome === "loss").length;
  const pending = parlays.filter((p) => p.outcome === "pending").length;

  let predicted = 0;
  let actual = 0;
  let legs = 0;
  for (const p of parlays) {
    p.legs.forEach((leg, i) => {
      const outcome = p.leg_outcomes[i] ?? "pending";
      if (outcome === "pending") return;
      legs += 1;
      predicted += leg.win_probability ?? 0.5;
      actual += outcome === "win" ? 1 : 0;
    });
  }

  const legHit = legs ? actual / legs : null;
  const modelRate = legs ? predicted / legs : null;

  return {
    total_parlays: parlays.length,
    pending,
    wins,
    losses,
    pushes: settled.length - wins - losses,
    leg_hit_rate: legHit,
    model_predicted_rate: modelRate,
    calibration_gap:
      legHit != null && modelRate != null ? legHit - modelRate : null,
  };
}

export function trimParlays(parlays: SavedParlayRecord[]): SavedParlayRecord[] {
  return parlays.slice(0, MAX_SAVED);
}

export function applyOutcomesBatch(
  state: TrackerState,
  parlayId: string,
  outcomes: { leg_index: number; outcome: Exclude<LegOutcome, "pending"> }[]
): TrackerState {
  let next = state;
  const parlay = state.parlays.find((p) => p.id === parlayId);
  if (!parlay) return state;

  let previousOutcome = parlay.outcome;
  for (const { leg_index, outcome } of outcomes) {
    next = {
      ...next,
      parlays: updateLegOutcomeLocal(next, parlayId, leg_index, outcome).parlays,
    };
  }

  const updated = next.parlays.find((p) => p.id === parlayId);
  if (!updated) return next;

  return settleBankroll(next, updated, previousOutcome);
}

export function removeParlay(state: TrackerState, parlayId: string): TrackerState {
  return { ...state, parlays: state.parlays.filter((p) => p.id !== parlayId) };
}

export type { TrackerState, RiskLevel };
