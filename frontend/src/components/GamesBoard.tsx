"use client";

import { useEffect, useState } from "react";
import { LineMovementChart } from "@/components/LineMovementChart";
import { fetchGames, formatAmerican, type GameSummary } from "@/lib/api";

type Props = {
  sport?: string;
};

export function GamesBoard({ sport }: Props) {
  const [games, setGames] = useState<GameSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    fetchGames(sport || undefined)
      .then(setGames)
      .catch(() => setGames([]))
      .finally(() => setLoading(false));
  }, [sport]);

  if (loading) {
    return <p className="text-sm text-zinc-600">Loading today&apos;s board…</p>;
  }

  if (games.length === 0) {
    return (
      <p className="text-sm text-zinc-600">No games on the board right now.</p>
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
