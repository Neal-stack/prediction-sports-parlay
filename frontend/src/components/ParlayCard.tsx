"use client";

import type { ParlayResponse } from "@/lib/api";
import { formatAmerican, formatPercent } from "@/lib/api";

type Props = {
  parlay: ParlayResponse;
};

const marketLabel: Record<string, string> = {
  moneyline: "ML",
  spread: "Spread",
  total: "Total",
};

const riskLabel: Record<string, string> = {
  safe: "Safe",
  balanced: "Balanced",
  bold: "Bold",
};

export function ParlayCard({ parlay }: Props) {
  return (
    <div className="w-full max-w-lg animate-in fade-in slide-in-from-bottom-2 duration-300">
      <div className="rounded-2xl border border-emerald-500/30 bg-emerald-950/20 p-6 shadow-lg shadow-emerald-900/10">
        <div className="mb-5 flex items-baseline justify-between gap-4">
          <div>
            <p className="text-xs font-medium uppercase tracking-widest text-emerald-400/80">
              {riskLabel[parlay.risk]}{" "}
              {parlay.same_game ? "same-game" : ""} parlay
            </p>
            <p className="mt-1 text-2xl font-semibold text-white">
              {parlay.legs.length} legs · {formatAmerican(parlay.combined_american)}
            </p>
            <p className="mt-1 text-sm text-zinc-400">
              ${parlay.payout_on_100.toFixed(0)} on a $100 bet
            </p>
          </div>
          <div className="text-right">
            <p className="text-xs text-zinc-500">Est. win</p>
            <p className="text-lg font-mono text-emerald-300">
              {formatPercent(parlay.estimated_win_prob)}
            </p>
            <p className="mt-1 text-xs text-zinc-600">
              Implied {formatPercent(parlay.combined_implied_prob)}
            </p>
          </div>
        </div>

        <ul className="space-y-3">
          {parlay.legs.map((leg, i) => (
            <li
              key={`${leg.game_id}-${leg.market}-${i}`}
              className="rounded-xl border border-zinc-800 bg-zinc-900/80 px-4 py-3"
            >
              <div className="flex items-start justify-between gap-2">
                <div>
                  <span className="text-xs font-medium text-zinc-500">
                    {leg.sport.toUpperCase()} · {marketLabel[leg.market]}
                  </span>
                  <p className="font-medium text-zinc-100">{leg.selection}</p>
                  <p className="text-sm text-zinc-500">{leg.matchup}</p>
                </div>
                <div className="shrink-0 text-right">
                  <span className="font-mono text-sm text-emerald-400">
                    {formatAmerican(leg.odds_american)}
                  </span>
                  <p className="text-xs text-zinc-500">
                    {formatPercent(leg.win_probability)} win
                  </p>
                </div>
              </div>
              <p className="mt-2 text-xs leading-relaxed text-zinc-400">
                {leg.rationale}
              </p>
            </li>
          ))}
        </ul>

        {parlay.ai_insight && (
          <div className="mt-4 rounded-xl border border-zinc-800 bg-zinc-900/60 px-4 py-3">
            <p className="text-xs font-medium uppercase tracking-wider text-zinc-500">
              AI insight
            </p>
            <p className="mt-1 text-sm leading-relaxed text-zinc-300">
              {parlay.ai_insight}
            </p>
          </div>
        )}

        <p className="mt-4 text-sm leading-relaxed text-zinc-500">{parlay.summary}</p>
        <p className="mt-2 text-xs text-zinc-600">
          No parlay is guaranteed. Picks optimize win probability for your risk level.
        </p>
      </div>
    </div>
  );
}
