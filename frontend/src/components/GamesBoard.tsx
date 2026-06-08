"use client";

import { useEffect, useRef, useState } from "react";
import { LineMovementChart } from "@/components/LineMovementChart";
import { formatAmerican, type GameSummary } from "@/lib/api";

type Props = {
  sport?: string;
  games?: GameSummary[];
  loading?: boolean;
  error?: string | null;
  onRetry?: () => void;
};

export function GamesBoard({
  sport,
  games: externalGames,
  loading: externalLoading,
  error,
  onRetry,
}: Props) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const [internalGames, setInternalGames] = useState<GameSummary[]>([]);
  const [internalLoading, setInternalLoading] = useState(!externalGames);
  const mounted = useRef(true);

  const games = externalGames ?? internalGames;
  const loading = externalLoading ?? internalLoading;

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    if (externalGames) return;
    setInternalLoading(true);
    import("@/lib/api")
      .then(({ fetchGames }) => fetchGames(sport || undefined))
      .then((list) => {
        if (mounted.current) setInternalGames(list);
      })
      .catch(() => {
        if (mounted.current) setInternalGames([]);
      })
      .finally(() => {
        if (mounted.current) setInternalLoading(false);
      });
  }, [sport, externalGames]);

  if (loading) {
    return <p className="text-sm text-zinc-600">Loading today&apos;s board…</p>;
  }

  if (error) {
    return (
      <div className="text-sm text-red-400" role="alert">
        {error}
        {onRetry && (
          <button
            type="button"
            onClick={onRetry}
            className="ml-2 underline hover:text-red-300"
          >
            Retry
          </button>
        )}
      </div>
    );
  }

  if (games.length === 0) {
    return (
      <p className="text-sm text-zinc-600">
        No games on the board right now.
        {onRetry && (
          <button
            type="button"
            onClick={onRetry}
            className="ml-2 text-zinc-500 underline hover:text-zinc-300"
          >
            Refresh
          </button>
        )}
      </p>
    );
  }

  return (
    <div className="w-full max-w-2xl">
      <p className="mb-3 text-xs font-medium uppercase tracking-widest text-zinc-500">
        Today&apos;s board · {games.length} games
      </p>
      <ul className="space-y-2">
        {games.map((g) => {
          const open = expanded === g.id;
          return (
            <li
              key={g.id}
              className="rounded-xl border border-zinc-800 bg-zinc-900/60 px-4 py-3"
            >
              <button
                type="button"
                onClick={() => setExpanded(open ? null : g.id)}
                className="flex w-full items-start justify-between gap-3 text-left"
              >
                <div>
                  <span className="text-xs font-medium text-zinc-500">
                    {g.sport.toUpperCase()}
                  </span>
                  <p className="font-medium text-zinc-100">
                    {g.away_team} @ {g.home_team}
                  </p>
                  <p className="text-xs text-zinc-600">
                    {new Date(g.start_time).toLocaleString(undefined, {
                      weekday: "short",
                      hour: "numeric",
                      minute: "2-digit",
                    })}
                  </p>
                </div>
                <div className="text-right text-xs font-mono text-zinc-400">
                  {g.moneyline_away != null && (
                    <p>Away {formatAmerican(g.moneyline_away)}</p>
                  )}
                  {g.moneyline_home != null && (
                    <p>Home {formatAmerican(g.moneyline_home)}</p>
                  )}
                  {g.spread_home != null && (
                    <p className="text-zinc-500">
                      {g.spread_home > 0 ? "+" : ""}
                      {g.spread_home}
                    </p>
                  )}
                  {g.total != null && (
                    <p className="text-zinc-500">O/U {g.total}</p>
                  )}
                </div>
              </button>
              {open && (
                <LineMovementChart gameId={g.id} homeTeam={g.home_team} />
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
