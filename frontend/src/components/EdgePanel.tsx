"use client";

import { useEffect, useRef, useState } from "react";
import {
  analyzeEdge,
  formatPercent,
  type EdgeAnalysisResponse,
  type ParlayResponse,
} from "@/lib/api";
import { useDebouncedCallback } from "@/hooks/useDebouncedCallback";

type Props = {
  parlay: ParlayResponse;
};

export function EdgePanel({ parlay }: Props) {
  const [probs, setProbs] = useState<number[]>(
    parlay.legs.map((l) => l.win_probability)
  );
  const [analysis, setAnalysis] = useState<EdgeAnalysisResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const runAnalysis = useDebouncedCallback(async (next: number[]) => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setLoading(true);
    setError(null);
    try {
      const result = await analyzeEdge(
        parlay,
        next.map((p, i) => ({ leg_index: i, user_probability: p }))
      );
      if (!controller.signal.aborted) setAnalysis(result);
    } catch (e) {
      if (!controller.signal.aborted) {
        setAnalysis(null);
        setError(e instanceof Error ? e.message : "Analysis failed");
      }
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }, 350);

  useEffect(() => {
    const initial = parlay.legs.map((l) => l.win_probability);
    setProbs(initial);
    runAnalysis(initial);
    return () => abortRef.current?.abort();
  }, [parlay, runAnalysis]);

  function updateProb(index: number, value: number) {
    const next = [...probs];
    next[index] = value;
    setProbs(next);
    runAnalysis(next);
  }

  return (
    <div className="w-full max-w-lg rounded-2xl border border-zinc-800 bg-zinc-900/50 p-5">
      <p className="text-xs font-medium uppercase tracking-widest text-zinc-500">
        Your edge
      </p>
      <p className="mt-1 text-sm text-zinc-400">
        Set your win probability per leg — we compare it to implied odds.
      </p>

      <ul className="mt-4 space-y-4">
        {parlay.legs.map((leg, i) => (
          <li key={`${leg.game_id}-${leg.market}`}>
            <div className="flex items-center justify-between text-sm">
              <label htmlFor={`edge-${i}`} className="text-zinc-200">
                {leg.selection}
              </label>
              <span className="font-mono text-zinc-400" aria-live="polite">
                {formatPercent(probs[i])}
              </span>
            </div>
            <input
              id={`edge-${i}`}
              type="range"
              min={5}
              max={95}
              value={Math.round(probs[i] * 100)}
              onChange={(e) => updateProb(i, Number(e.target.value) / 100)}
              aria-valuenow={Math.round(probs[i] * 100)}
              aria-valuemin={5}
              aria-valuemax={95}
              className="mt-2 w-full accent-emerald-500"
            />
            <div className="mt-1 flex justify-between text-xs text-zinc-600">
              <span>Implied {formatPercent(leg.implied_prob)}</span>
              <span>Model {formatPercent(leg.win_probability)}</span>
            </div>
          </li>
        ))}
      </ul>

      {loading && <p className="mt-3 text-xs text-zinc-600">Recalculating…</p>}
      {error && (
        <p className="mt-3 text-xs text-red-400" role="alert">
          {error}
        </p>
      )}

      {analysis && !loading && (
        <div
          className="mt-4 rounded-xl border border-zinc-800 bg-zinc-950/60 px-4 py-3"
          aria-live="polite"
        >
          <p className="text-sm text-zinc-300">
            Your parlay win rate:{" "}
            <span className="font-mono text-emerald-400">
              {formatPercent(analysis.user_estimated_win_prob)}
            </span>
          </p>
          <p className="mt-1 text-xs text-zinc-500">{analysis.summary}</p>
        </div>
      )}
    </div>
  );
}
