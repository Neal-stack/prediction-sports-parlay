"use client";

import { useEffect, useState } from "react";
import {
  formatAmerican,
  formatPercent,
  type ParlayResponse,
  type SavedBet,
} from "@/lib/api";

const STORAGE_KEY = "parlay-bankroll-v1";
const MAX_STAKE_PCT = 0.05;

type BankrollState = {
  bankroll: number;
  bets: SavedBet[];
};

type Props = {
  parlay: ParlayResponse | null;
};

function loadState(): BankrollState {
  if (typeof window === "undefined") return { bankroll: 500, bets: [] };
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { bankroll: 500, bets: [] };
    return JSON.parse(raw) as BankrollState;
  } catch {
    return { bankroll: 500, bets: [] };
  }
}

function saveState(state: BankrollState) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
}

export function BankrollPanel({ parlay }: Props) {
  const [bankroll, setBankroll] = useState(500);
  const [bets, setBets] = useState<SavedBet[]>([]);
  const [stake, setStake] = useState(25);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    const s = loadState();
    setBankroll(s.bankroll);
    setBets(s.bets);
  }, []);

  function persist(next: BankrollState) {
    setBankroll(next.bankroll);
    setBets(next.bets);
    saveState(next);
  }

  function saveParlay() {
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

    const bet: SavedBet = {
      id: crypto.randomUUID(),
      stake,
      combined_american: parlay.combined_american,
      estimated_win_prob: parlay.estimated_win_prob,
      legs: parlay.legs.length,
      risk: parlay.risk,
      outcome: "pending",
      saved_at: new Date().toISOString(),
      potential_payout:
        parlay.combined_american > 0
          ? stake * (parlay.combined_american / 100)
          : stake * (100 / Math.abs(parlay.combined_american)),
    };

    persist({ bankroll, bets: [bet, ...bets].slice(0, 20) });
    setMessage("Parlay saved to your tracker.");
  }

  function settle(id: string, outcome: "win" | "loss" | "push") {
    const bet = bets.find((b) => b.id === id);
    if (!bet) return;

    let nextBankroll = bankroll;
    if (outcome === "win") nextBankroll += bet.potential_payout;
    if (outcome === "loss") nextBankroll -= bet.stake;

    const nextBets = bets.map((b) =>
      b.id === id ? { ...b, outcome } : b
    );
    persist({ bankroll: Math.max(0, nextBankroll), bets: nextBets });
    setMessage(null);
  }

  const maxStake = bankroll * MAX_STAKE_PCT;
  const pending = bets.filter((b) => b.outcome === "pending").length;

  return (
    <div className="w-full max-w-lg rounded-2xl border border-zinc-800 bg-zinc-900/50 p-5">
      <p className="text-xs font-medium uppercase tracking-widest text-zinc-500">
        Bankroll tracker
      </p>

      <div className="mt-3 flex items-end gap-3">
        <div>
          <label className="text-xs text-zinc-600">Bankroll ($)</label>
          <input
            type="number"
            min={0}
            value={bankroll}
            onChange={(e) =>
              persist({ bankroll: Number(e.target.value), bets })
            }
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
            className="w-24 rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm text-zinc-200"
          />
          <button
            type="button"
            onClick={saveParlay}
            className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-500"
          >
            Save parlay
          </button>
        </div>
      )}

      {message && <p className="mt-2 text-xs text-amber-400">{message}</p>}

      <p className="mt-3 text-xs text-zinc-600">
        Gamble responsibly. Never bet more than you can afford to lose. 5% max
        stake enforced.
      </p>

      {bets.length > 0 && (
        <ul className="mt-4 space-y-2">
          {bets.slice(0, 5).map((b) => (
            <li
              key={b.id}
              className="rounded-lg border border-zinc-800 bg-zinc-950/60 px-3 py-2 text-xs"
            >
              <div className="flex justify-between text-zinc-300">
                <span>
                  {b.legs}-leg {b.risk} · {formatAmerican(b.combined_american)}
                </span>
                <span>${b.stake.toFixed(0)} → ${b.potential_payout.toFixed(0)}</span>
              </div>
              <p className="text-zinc-600">
                Est. {formatPercent(b.estimated_win_prob)} · {b.outcome}
              </p>
              {b.outcome === "pending" && (
                <div className="mt-2 flex gap-2">
                  {(["win", "loss", "push"] as const).map((o) => (
                    <button
                      key={o}
                      type="button"
                      onClick={() => settle(b.id, o)}
                      className="rounded bg-zinc-800 px-2 py-1 text-[10px] uppercase text-zinc-400 hover:text-zinc-200"
                    >
                      {o}
                    </button>
                  ))}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
