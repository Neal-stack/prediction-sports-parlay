"use client";

import type { ParlayResponse, PickLeg } from "@/lib/api";
import { formatAmerican, formatPercent } from "@/lib/api";

type Props = {
  parlay: ParlayResponse;
  onAddAnchor?: (leg: PickLeg) => void;
};

const marketLabel: Record<string, string> = {
  moneyline: "ML",
  spread: "Spread",
  total: "Total",
  player_prop: "Prop",
};

const riskLabel: Record<string, string> = {
  safe: "Safe",
  balanced: "Balanced",
  bold: "Bold",
};

function edgeColor(edge: number): string {
  if (edge >= 0.04) return "text-emerald-400";
  if (edge > 0) return "text-emerald-500/80";
  if (edge <= -0.04) return "text-red-400";
  return "text-zinc-500";
}

function LegRow({ leg }: { leg: PickLeg }) {
  const edge = leg.edge ?? leg.win_probability - leg.implied_prob;
  const fallback = leg.model_source === "market_fallback";
  const strongEdge = edge >= 0.04;
  return (
    <li className="rounded-xl border border-zinc-800 bg-zinc-900/80 px-4 py-3">
      <div className="flex items-start justify-between gap-2">
        <div>
          <span className="text-xs font-medium text-zinc-500">
            {leg.sport.toUpperCase()} · {marketLabel[leg.market] ?? leg.market}
          </span>
          <p className="font-medium text-zinc-100">{leg.selection}</p>
          <p className="text-sm text-zinc-500">{leg.matchup}</p>
        </div>
        <div className="shrink-0 text-right">
          <span className="font-mono text-sm text-emerald-400">
            {formatAmerican(leg.odds_american)}
          </span>
        </div>
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
        <span className="text-zinc-500">
          Implied <span className="font-mono text-zinc-300">{formatPercent(leg.implied_prob)}</span>
        </span>
        <span className="text-zinc-500">
          Model <span className="font-mono text-zinc-200">{formatPercent(leg.win_probability)}</span>
        </span>
        {/* Prop odds are derived from our own probability, so "edge" there is
            just the vig, not a measured disagreement with a book. Show the
            break-even price instead — that is the number to act on. */}
        {leg.market !== "player_prop" && (
          <span className={edgeColor(edge)}>
            Edge <span className="font-mono">{edge >= 0 ? "+" : ""}{(edge * 100).toFixed(1)}%</span>
          </span>
        )}
        {fallback && (
          <span className="rounded bg-amber-950/50 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-amber-400">
            market only
          </span>
        )}
        {leg.line_source === "book" && leg.book && (
          <span className="rounded bg-emerald-950/50 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-emerald-400">
            {leg.book} line
          </span>
        )}
        {leg.market === "player_prop" && leg.line_source !== "book" && leg.fair_odds_american != null && (
          <span className="text-zinc-500">
            Need better than{" "}
            <span className="font-mono text-amber-300">
              {formatAmerican(leg.fair_odds_american)}
            </span>
          </span>
        )}
        {leg.availability != null && leg.availability < 0.97 && (
          <span className="rounded bg-amber-950/50 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-amber-400">
            injury risk
          </span>
        )}
      </div>

      {strongEdge && !fallback && (
        <p className="mt-2 rounded-md border border-emerald-500/20 bg-emerald-950/30 px-2 py-1 text-[11px] text-emerald-300">
          ⬆ Model is {((edge) * 100).toFixed(1)}% above the market here — that&apos;s the value.
        </p>
      )}

      <p className="mt-2 text-xs leading-relaxed text-zinc-400">{leg.rationale}</p>
    </li>
  );
}

export function ParlayCard({ parlay, onAddAnchor }: Props) {
  const anchors = parlay.anchors ?? [];
  const allProps =
    parlay.legs.length > 0 && parlay.legs.every((l) => l.market === "player_prop");
  return (
    <div className="w-full max-w-lg animate-in fade-in slide-in-from-bottom-2 duration-300">
      <div className="rounded-2xl border border-emerald-500/30 bg-emerald-950/20 p-6 shadow-lg shadow-emerald-900/10">
        <div className="mb-5 flex items-baseline justify-between gap-4">
          <div>
            <p className="text-xs font-medium uppercase tracking-widest text-emerald-400/80">
              {riskLabel[parlay.risk]}{" "}
              {allProps ? "player-prop" : parlay.same_game ? "same-game" : ""} parlay
            </p>
            <p className="mt-1 text-2xl font-semibold text-white">
              {parlay.legs.length} legs · {formatAmerican(parlay.combined_american)}
            </p>
            <p className="mt-1 text-sm text-zinc-400">
              ${parlay.payout_on_100.toFixed(0)} on a $100 bet
            </p>
          </div>
          <div className="text-right">
            <p className="text-xs text-zinc-500">Model win</p>
            <p className="text-lg font-mono text-emerald-300">
              {formatPercent(parlay.correlated_win_prob ?? parlay.estimated_win_prob)}
            </p>
            <p className="mt-1 text-xs text-zinc-600">
              Implied {formatPercent(parlay.combined_implied_prob)}
            </p>
            {parlay.fair_combined_american != null && (
              <p className="mt-1 text-xs text-zinc-600">
                Fair {formatAmerican(parlay.fair_combined_american)}
              </p>
            )}
          </div>
        </div>

        {parlay.book_check_passed !== false ? (
          <p className="mb-3 inline-flex items-center gap-1 rounded-full bg-emerald-950/40 px-2.5 py-1 text-[11px] text-emerald-400">
            ✓ Book-check passed · no conflicting legs
          </p>
        ) : (
          <p className="mb-3 inline-flex items-center gap-1 rounded-full bg-amber-950/40 px-2.5 py-1 text-[11px] text-amber-400">
            ⚠ Review legs — possible book conflict
          </p>
        )}

        {parlay.expected_value_per_100 != null && (
          <p
            className={`mb-3 text-xs ${
              parlay.expected_value_per_100 >= 0 ? "text-emerald-400" : "text-amber-400"
            }`}
          >
            Expected value at this price:{" "}
            <span className="font-mono">
              {parlay.expected_value_per_100 >= 0 ? "+" : ""}
              ${parlay.expected_value_per_100.toFixed(2)}
            </span>{" "}
            per $100 staked
          </p>
        )}

        {parlay.ev_warning && (
          <p className="mb-3 rounded-lg border border-amber-500/20 bg-amber-950/20 px-3 py-2 text-[11px] leading-relaxed text-amber-300/90">
            {parlay.ev_warning}
          </p>
        )}

        <ul className="space-y-3">
          {parlay.legs.map((leg, i) => (
            <LegRow key={`${leg.game_id}-${leg.market}-${i}`} leg={leg} />
          ))}
        </ul>

        {anchors.length > 0 && (
          <div className="mt-5">
            <p className="text-xs font-medium uppercase tracking-wider text-zinc-500">
              Suggested anchors (optional add-ons)
            </p>
            <ul className="mt-2 space-y-2">
              {anchors.map((a, i) => {
                const edge = a.edge ?? a.win_probability - a.implied_prob;
                return (
                  <li
                    key={`anchor-${i}`}
                    className="rounded-lg border border-zinc-800 bg-zinc-900/60 px-3 py-2"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <p className="text-sm text-zinc-200">{a.selection}</p>
                      <div className="flex shrink-0 items-center gap-2">
                        <span className="font-mono text-xs text-emerald-400">
                          {formatPercent(a.win_probability)}
                        </span>
                        {onAddAnchor && (
                          <button
                            type="button"
                            onClick={() => onAddAnchor(a)}
                            className="rounded bg-emerald-700 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-white hover:bg-emerald-600"
                          >
                            + Add
                          </button>
                        )}
                      </div>
                    </div>
                    <p className="mt-1 text-[11px] text-zinc-500">
                      {a.rationale} · model edge {edge >= 0 ? "+" : ""}
                      {(edge * 100).toFixed(1)}%
                    </p>
                  </li>
                );
              })}
            </ul>
          </div>
        )}

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
          No parlay is guaranteed. Picks are driven by an independent model — odds are used
          only for payout math.
        </p>
      </div>
    </div>
  );
}
