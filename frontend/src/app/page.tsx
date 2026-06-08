"use client";

import { useCallback, useEffect, useState } from "react";
import { ChatPanel } from "@/components/ChatPanel";
import { EdgePanel } from "@/components/EdgePanel";
import { GamesBoard } from "@/components/GamesBoard";
import { ParlayCard } from "@/components/ParlayCard";
import { ParlayTrackerPanel } from "@/components/ParlayTrackerPanel";
import {
  fetchGames,
  fetchStatus,
  generateParlay,
  RISK_OPTIONS,
  type GameSummary,
  type ParlayResponse,
  type RiskLevel,
  type StatusResponse,
} from "@/lib/api";

const SPORTS = [
  { value: "", label: "All sports" },
  { value: "nba", label: "NBA" },
  { value: "nfl", label: "NFL" },
  { value: "mlb", label: "MLB" },
  { value: "nhl", label: "NHL" },
];

export default function Home() {
  const [parlay, setParlay] = useState<ParlayResponse | null>(null);
  const [status, setStatus] = useState<StatusResponse | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [legCount, setLegCount] = useState(3);
  const [risk, setRisk] = useState<RiskLevel>("balanced");
  const [sport, setSport] = useState("");
  const [parlayMode, setParlayMode] = useState<"multi" | "same-game">("multi");
  const [games, setGames] = useState<GameSummary[]>([]);
  const [gamesLoading, setGamesLoading] = useState(true);
  const [gamesError, setGamesError] = useState<string | null>(null);
  const [gameId, setGameId] = useState("");

  const loadStatus = useCallback(() => {
    fetchStatus()
      .then(setStatus)
      .catch((e) =>
        setStatusError(e instanceof Error ? e.message : "Backend unreachable")
      );
  }, []);

  const loadGames = useCallback(() => {
    setGamesLoading(true);
    setGamesError(null);
    const filter = parlayMode === "same-game" ? sport || "nba" : sport || undefined;
    fetchGames(filter)
      .then((list) => {
        setGames(list);
        if (parlayMode === "same-game") {
          setGameId((prev) =>
            prev && list.some((g) => g.id === prev) ? prev : list[0]?.id ?? ""
          );
        }
      })
      .catch((e) => {
        setGames([]);
        setGameId("");
        setGamesError(e instanceof Error ? e.message : "Failed to load games");
      })
      .finally(() => setGamesLoading(false));
  }, [sport, parlayMode]);

  useEffect(() => {
    loadStatus();
  }, [loadStatus]);

  useEffect(() => {
    loadGames();
  }, [loadGames]);

  async function handleGenerate() {
    setLoading(true);
    setError(null);
    try {
      const result = await generateParlay({
        legs: legCount,
        sport: parlayMode === "same-game" ? sport || "nba" : sport || null,
        risk,
        game_id: parlayMode === "same-game" ? gameId : null,
      });
      setParlay(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong");
      setParlay(null);
    } finally {
      setLoading(false);
    }
  }

  const live = status && !status.demo_mode && status.sharpapi;
  const sameGameGames =
    parlayMode === "same-game" ? games : games;

  return (
    <main className="flex flex-1 flex-col items-center px-4 py-12 sm:py-16">
      <div className="mb-8 max-w-md text-center">
        <h1 className="text-3xl font-semibold tracking-tight text-white sm:text-4xl">
          Parlay
        </h1>
        <p className="mt-3 text-zinc-400">
          Live odds, line movement, injuries, and news — optimized for your risk
          level.
        </p>
        {status && (
          <p className="mt-2 text-xs text-zinc-600">
            {live
              ? `Live · ${status.games_cached} games · ${status.games_source ?? "unknown"}`
              : "Check backend/.env for live data"}
            {status.ai_provider
              ? ` · AI: ${status.ai_provider}`
              : " · AI offline"}
            {status.tracking_enabled ? " · tracking on" : ""}
            {status.calibration_samples
              ? ` · ${status.calibration_samples} calibration legs`
              : ""}
          </p>
        )}
        {statusError && (
          <p className="mt-1 text-xs text-amber-500" role="alert">
            {statusError}
          </p>
        )}
        {status?.last_odds_sync_error && (
          <p className="mt-1 text-xs text-amber-600">
            Odds sync: {status.last_odds_sync_error}
          </p>
        )}
      </div>

      <div className="mb-6 flex w-full max-w-lg flex-wrap items-center justify-center gap-4">
        <div className="flex items-center gap-2">
          <label htmlFor="mode" className="text-sm text-zinc-500">
            Type
          </label>
          <select
            id="mode"
            value={parlayMode}
            onChange={(e) =>
              setParlayMode(e.target.value as "multi" | "same-game")
            }
            className="rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-200 outline-none focus:border-emerald-500/50"
          >
            <option value="multi">Multi-game</option>
            <option value="same-game">Same-game (SGP)</option>
          </select>
        </div>

        <div className="flex items-center gap-2">
          <label htmlFor="risk" className="text-sm text-zinc-500">
            Risk
          </label>
          <select
            id="risk"
            value={risk}
            onChange={(e) => setRisk(e.target.value as RiskLevel)}
            className="rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-200 outline-none focus:border-emerald-500/50"
          >
            {RISK_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </div>

        <div className="flex items-center gap-2">
          <label htmlFor="sport" className="text-sm text-zinc-500">
            Sport
          </label>
          <select
            id="sport"
            value={sport}
            onChange={(e) => setSport(e.target.value)}
            className="rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-200 outline-none focus:border-emerald-500/50"
          >
            {SPORTS.map((s) => (
              <option key={s.value || "all"} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </div>

        <div className="flex items-center gap-2">
          <label htmlFor="legs" className="text-sm text-zinc-500">
            Legs
          </label>
          <select
            id="legs"
            value={legCount}
            onChange={(e) => setLegCount(Number(e.target.value))}
            className="rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-200 outline-none focus:border-emerald-500/50"
          >
            {[2, 3, 4, 5].map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </div>
      </div>

      {parlayMode === "same-game" && (
        <div className="mb-4 flex w-full max-w-lg flex-col items-center gap-2">
          <label htmlFor="game" className="text-sm text-zinc-500">
            Matchup
          </label>
          <select
            id="game"
            value={gameId}
            onChange={(e) => setGameId(e.target.value)}
            className="w-full max-w-md rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-200 outline-none focus:border-emerald-500/50"
          >
            {sameGameGames.length === 0 && (
              <option value="">No games — try NBA sport filter</option>
            )}
            {sameGameGames.map((g) => (
              <option key={g.id} value={g.id}>
                {g.away_team} @ {g.home_team}
              </option>
            ))}
          </select>
          <p className="text-xs text-zinc-600">
            Stacks ML, spread & total on one game (e.g. Finals tonight).
          </p>
        </div>
      )}

      <p className="mb-6 max-w-md text-center text-xs text-zinc-600">
        {parlayMode === "same-game"
          ? "Same-game legs are correlated — higher payout, harder to hit."
          : RISK_OPTIONS.find((o) => o.value === risk)?.hint}
      </p>

      <button
        type="button"
        onClick={handleGenerate}
        disabled={loading}
        className="rounded-full bg-emerald-500 px-10 py-4 text-base font-semibold text-emerald-950 transition hover:bg-emerald-400 disabled:cursor-not-allowed disabled:opacity-50"
      >
        {loading ? "Building…" : "Generate parlay"}
      </button>

      {error && (
        <p className="mt-6 text-sm text-red-400" role="alert">
          {error}
        </p>
      )}

      <div className="mt-12 flex w-full flex-col items-center gap-10">
        <GamesBoard
          sport={sport}
          games={games}
          loading={gamesLoading}
          error={gamesError}
          onRetry={loadGames}
        />

        {parlay && (
          <div className="flex w-full max-w-lg flex-col items-center gap-6">
            <ParlayCard parlay={parlay} />
            <EdgePanel parlay={parlay} />
            <ParlayTrackerPanel
              parlay={parlay}
              trackingEnabled={status?.tracking_enabled}
            />
            <ChatPanel parlay={parlay} />
          </div>
        )}

        {!parlay && (
          <>
            <ParlayTrackerPanel trackingEnabled={status?.tracking_enabled} parlay={null} />
            <ChatPanel parlay={null} />
          </>
        )}
      </div>
    </main>
  );
}
