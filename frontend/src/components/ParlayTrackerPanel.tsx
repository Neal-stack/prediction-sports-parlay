"use client";

import { useCallback, useEffect, useState } from "react";
import {
  confirmSettlement,
  fetchPerformance,
  fetchTrackedParlays,
  formatAmerican,
  formatPercent,
  saveTrackedParlay,
  suggestSettlement,
  updateLegOutcome,
  type LegOutcome,
  type LegSettlementSuggestion,
  type ParlayResponse,
  type PerformanceStats,
  type SavedParlayRecord,
  type SettlementSuggestionResponse,
} from "@/lib/api";
import { getSessionId } from "@/lib/session";
import {
  applyOutcomesBatch,
  createLocalParlay,
  loadTrackerState,
  localPerformance,
  potentialPayout,
  removeParlay,
  saveTrackerState,
  settleBankroll,
  trimParlays,
  updateLegOutcomeLocal,
} from "@/lib/tracker";

const MAX_STAKE_PCT = 0.05;

type Props = {
  parlay: ParlayResponse | null;
  trackingEnabled?: boolean;
};

export function ParlayTrackerPanel({ parlay, trackingEnabled }: Props) {
  const [bankroll, setBankroll] = useState(500);
  const [saved, setSaved] = useState<SavedParlayRecord[]>([]);
  const [stake, setStake] = useState(25);
  const [message, setMessage] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [stats, setStats] = useState<PerformanceStats | null>(null);
  const [checkingId, setCheckingId] = useState<string | null>(null);
  const [suggestions, setSuggestions] = useState<
    Record<string, SettlementSuggestionResponse>
  >({});
  const sessionId = getSessionId();

  const persist = useCallback((nextBankroll: number, nextSaved: SavedParlayRecord[]) => {
    const trimmed = trimParlays(nextSaved);
    setBankroll(nextBankroll);
    setSaved(trimmed);
    saveTrackerState({ bankroll: nextBankroll, parlays: trimmed });
    setStats(localPerformance(trimmed));
  }, []);

  useEffect(() => {
    const local = loadTrackerState();
    setBankroll(local.bankroll);
    setSaved(local.parlays);
    setStats(localPerformance(local.parlays));

    if (!trackingEnabled) return;

    const controller = new AbortController();
    fetchTrackedParlays(sessionId)
      .then((remote) => {
        if (controller.signal.aborted || remote.length === 0) return;
        const merged = [...remote];
        for (const localP of local.parlays) {
          if (!merged.some((r) => r.id === localP.id)) {
            merged.push(localP);
          }
        }
        const ordered = trimParlays(
          merged.sort(
            (a, b) =>
              new Date(b.saved_at).getTime() - new Date(a.saved_at).getTime()
          )
        );
        persist(local.bankroll, ordered);
      })
      .catch(() => {});

    fetchPerformance(sessionId)
      .then((remoteStats) => {
        if (!controller.signal.aborted && remoteStats) setStats(remoteStats);
      })
      .catch(() => {});

    return () => controller.abort();
  }, [sessionId, trackingEnabled, persist]);

  async function handleSave() {
    if (!parlay) return;
    const maxStake = bankroll * MAX_STAKE_PCT;
    if (stake > maxStake) {
      setMessage(`Keep stakes ≤5% of bankroll ($${maxStake.toFixed(0)} max).`);
      return;
    }
    if (stake <= 0 || stake > bankroll) {
      setMessage("Invalid stake amount.");
      return;
    }

    let record = createLocalParlay(parlay, stake);
    if (trackingEnabled) {
      try {
        record = await saveTrackedParlay(sessionId, parlay, stake);
      } catch {
        setMessage("Saved locally — backend sync unavailable.");
      }
    }

    persist(bankroll, [record, ...saved]);
    setMessage("Parlay saved. Check results tomorrow to confirm and calibrate.");
    setExpanded(record.id);
  }

  async function handleLegOutcome(
    parlayRecord: SavedParlayRecord,
    legIndex: number,
    outcome: Exclude<LegOutcome, "pending">
  ) {
    const previousOutcome = parlayRecord.outcome;
    let updated = updateLegOutcomeLocal(
      { bankroll, parlays: saved },
      parlayRecord.id,
      legIndex,
      outcome
    ).parlays.find((p) => p.id === parlayRecord.id);

    if (!updated) return;

    if (trackingEnabled) {
      const remote = await updateLegOutcome(
        sessionId,
        parlayRecord.id,
        legIndex,
        outcome
      );
      if (remote) updated = remote;
    }

    const nextState = settleBankroll(
      { bankroll, parlays: saved.map((p) => (p.id === updated!.id ? updated! : p)) },
      updated,
      previousOutcome
    );
    persist(nextState.bankroll, nextState.parlays);
    setSuggestions((prev) => {
      const next = { ...prev };
      delete next[parlayRecord.id];
      return next;
    });
    setMessage(
      updated.outcome !== "pending"
        ? `Parlay marked ${updated.outcome}. Results feed model calibration.`
        : null
    );
  }

  async function handleCheckResults(parlayRecord: SavedParlayRecord) {
    setCheckingId(parlayRecord.id);
    setMessage(null);
    try {
      const result = await suggestSettlement(
        parlayRecord.legs,
        parlayRecord.leg_outcomes
      );
      setSuggestions((prev) => ({ ...prev, [parlayRecord.id]: result }));
      setExpanded(parlayRecord.id);
      if (result.ready) {
        setMessage("Final scores found — review and confirm below.");
      } else {
        setMessage(result.message ?? "Games not final yet. Try again later.");
      }
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Could not check results");
    } finally {
      setCheckingId(null);
    }
  }

  async function handleConfirmAll(parlayRecord: SavedParlayRecord) {
    const suggestion = suggestions[parlayRecord.id];
    if (!suggestion?.ready) return;

    const outcomes = suggestion.suggestions
      .filter(
        (s): s is LegSettlementSuggestion & { outcome: Exclude<LegOutcome, "pending"> } =>
          s.ready && !!s.outcome
      )
      .map((s) => ({ leg_index: s.leg_index, outcome: s.outcome }));

    if (outcomes.length === 0) return;

    try {
      if (trackingEnabled) {
        const updated = await confirmSettlement(
          sessionId,
          parlayRecord.id,
          outcomes
        );
        const nextSaved = saved.map((p) =>
          p.id === updated.id ? updated : p
        );
        const previousOutcome = parlayRecord.outcome;
        const nextState = settleBankroll(
          { bankroll, parlays: nextSaved },
          updated,
          previousOutcome
        );
        persist(nextState.bankroll, nextState.parlays);
      } else {
        const nextState = applyOutcomesBatch(
          { bankroll, parlays: saved },
          parlayRecord.id,
          outcomes
        );
        persist(nextState.bankroll, nextState.parlays);
      }

      setSuggestions((prev) => {
        const next = { ...prev };
        delete next[parlayRecord.id];
        return next;
      });
      setMessage("Results confirmed — model calibration updated.");
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Confirm failed");
    }
  }

  function suggestedOutcome(
    parlayId: string,
    legIndex: number
  ): LegSettlementSuggestion | undefined {
    return suggestions[parlayId]?.suggestions.find((s) => s.leg_index === legIndex);
  }

  function handleRemove(parlayId: string) {
    const next = removeParlay({ bankroll, parlays: saved }, parlayId);
    persist(next.bankroll, next.parlays);
    if (expanded === parlayId) setExpanded(null);
    setMessage("Parlay removed from tracker.");
  }

  const maxStake = bankroll * MAX_STAKE_PCT;
  const pending = saved.filter((p) => p.outcome === "pending").length;

  return (
    <div className="w-full max-w-lg rounded-2xl border border-zinc-800 bg-zinc-900/50 p-5">
      <p className="text-xs font-medium uppercase tracking-widest text-zinc-500">
        Parlay tracker
      </p>
      <p className="mt-1 text-sm text-zinc-400">
        Save slips today. When games finish, tap Check results — we grade each leg from final scores; you confirm.
      </p>

      {stats && stats.total_parlays > 0 && (
        <div
          className="mt-3 rounded-xl border border-zinc-800 bg-zinc-950/60 px-4 py-3 text-xs text-zinc-400"
          aria-live="polite"
        >
          <p>
            Record: {stats.wins}W · {stats.losses}L · {stats.pending} pending
          </p>
          {stats.leg_hit_rate != null && stats.model_predicted_rate != null && (
            <p className="mt-1">
              Leg hit rate {formatPercent(stats.leg_hit_rate)} vs model{" "}
              {formatPercent(stats.model_predicted_rate)}
              {stats.calibration_gap != null && (
                <span
                  className={
                    stats.calibration_gap >= 0 ? "text-emerald-400" : "text-amber-400"
                  }
                >
                  {" "}
                  ({stats.calibration_gap >= 0 ? "+" : ""}
                  {(stats.calibration_gap * 100).toFixed(1)}%)
                </span>
              )}
            </p>
          )}
        </div>
      )}

      <div className="mt-3 flex items-end gap-3">
        <div>
          <label htmlFor="bankroll" className="text-xs text-zinc-600">
            Bankroll ($)
          </label>
          <input
            id="bankroll"
            type="number"
            min={0}
            value={bankroll}
            onChange={(e) => persist(Number(e.target.value), saved)}
            className="mt-1 block w-28 rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm text-zinc-200"
          />
        </div>
        <p className="pb-2 text-sm text-zinc-400">
          {pending} pending · max stake ${maxStake.toFixed(0)}
        </p>
      </div>

      {parlay && (
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <input
            type="number"
            min={1}
            max={maxStake}
            value={stake}
            onChange={(e) => setStake(Number(e.target.value))}
            aria-label="Stake amount"
            className="w-24 rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm text-zinc-200"
          />
          <button
            type="button"
            onClick={handleSave}
            className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-500"
          >
            Save parlay
          </button>
        </div>
      )}

      {message && (
        <p className="mt-2 text-xs text-amber-400" role="status">
          {message}
        </p>
      )}

      <p className="mt-3 text-xs text-zinc-600">
        Gamble responsibly. 5% max stake enforced.
      </p>

      {saved.length > 0 && (
        <ul className="mt-4 space-y-2">
          {saved.slice(0, 10).map((p) => {
            const open = expanded === p.id;
            const payout = potentialPayout(p.stake, p.combined_american);
            const slipSuggestions = suggestions[p.id];
            const hasPendingLegs = p.leg_outcomes.some((o) => o === "pending");

            return (
              <li
                key={p.id}
                className="rounded-lg border border-zinc-800 bg-zinc-950/60 px-3 py-2 text-xs"
              >
                <button
                  type="button"
                  onClick={() => setExpanded(open ? null : p.id)}
                  className="flex w-full justify-between text-left text-zinc-300"
                >
                  <span>
                    {p.legs.length}-leg {p.risk} ·{" "}
                    {formatAmerican(p.combined_american)} ·{" "}
                    <span
                      className={
                        p.outcome === "win"
                          ? "text-emerald-400"
                          : p.outcome === "loss"
                            ? "text-red-400"
                            : "text-zinc-500"
                      }
                    >
                      {p.outcome}
                    </span>
                  </span>
                  <span>
                    ${p.stake.toFixed(0)} → ${payout.toFixed(0)}
                  </span>
                </button>
                <p className="text-zinc-600">
                  {new Date(p.saved_at).toLocaleDateString(undefined, {
                    month: "short",
                    day: "numeric",
                    hour: "numeric",
                    minute: "2-digit",
                  })}{" "}
                  · Est. {formatPercent(p.estimated_win_prob)}
                </p>

                {p.outcome === "pending" && hasPendingLegs && (
                  <div className="mt-2 flex flex-wrap gap-2">
                    <button
                      type="button"
                      disabled={checkingId === p.id}
                      onClick={() => handleCheckResults(p)}
                      className="rounded bg-zinc-800 px-2 py-1 text-[10px] uppercase tracking-wide text-zinc-300 hover:bg-zinc-700 disabled:opacity-50"
                    >
                      {checkingId === p.id ? "Checking…" : "Check results"}
                    </button>
                    {slipSuggestions?.ready && (
                      <button
                        type="button"
                        onClick={() => handleConfirmAll(p)}
                        className="rounded bg-emerald-700 px-2 py-1 text-[10px] uppercase tracking-wide text-white hover:bg-emerald-600"
                      >
                        Confirm all
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={() => handleRemove(p.id)}
                      className="rounded bg-zinc-900 px-2 py-1 text-[10px] uppercase tracking-wide text-red-400 hover:bg-zinc-800"
                    >
                      Remove
                    </button>
                  </div>
                )}

                {open && (
                  <ul className="mt-3 space-y-2 border-t border-zinc-800 pt-2">
                    {p.legs.map((leg, i) => {
                      const legOutcome = p.leg_outcomes[i] ?? "pending";
                      const suggested = suggestedOutcome(p.id, i);
                      return (
                        <li
                          key={`${leg.game_id}-${leg.market}-${i}`}
                          className="rounded-md bg-zinc-900/80 px-2 py-2"
                        >
                          <p className="text-zinc-200">{leg.selection}</p>
                          <p className="text-zinc-600">{leg.matchup}</p>
                          {suggested && legOutcome === "pending" && (
                            <p
                              className={`mt-1 text-[11px] ${
                                suggested.ready ? "text-emerald-400" : "text-zinc-500"
                              }`}
                            >
                              {suggested.reason}
                            </p>
                          )}
                          <div className="mt-2 flex flex-wrap items-center gap-2">
                            <span className="text-zinc-500">
                              {legOutcome === "pending"
                                ? suggested?.ready && suggested.outcome
                                  ? `Suggested: ${suggested.outcome}`
                                  : "Result?"
                                : legOutcome}
                            </span>
                            {legOutcome === "pending" &&
                              (["win", "loss", "push"] as const).map((o) => (
                                <button
                                  key={o}
                                  type="button"
                                  onClick={() => handleLegOutcome(p, i, o)}
                                  className={`rounded px-2 py-1 text-[10px] uppercase ${
                                    suggested?.outcome === o
                                      ? "bg-emerald-900/60 text-emerald-300 ring-1 ring-emerald-600"
                                      : "bg-zinc-800 text-zinc-400 hover:bg-zinc-700 hover:text-zinc-200"
                                  }`}
                                >
                                  {o}
                                </button>
                              ))}
                          </div>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
