"use client";

import { useEffect, useState } from "react";
import { fetchLineMovement, type LineMovementPoint } from "@/lib/api";

type Props = {
  gameId: string;
  homeTeam: string;
};

function impliedFromAmerican(odds: number): number {
  if (odds > 0) return 100 / (odds + 100);
  return Math.abs(odds) / (Math.abs(odds) + 100);
}

export function LineMovementChart({ gameId, homeTeam }: Props) {
  const [points, setPoints] = useState<LineMovementPoint[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetchLineMovement(gameId)
      .then(setPoints)
      .catch(() => setPoints([]))
      .finally(() => setLoading(false));
  }, [gameId]);

  if (loading) {
    return <p className="text-xs text-zinc-600">Loading line history…</p>;
  }

  if (points.length < 2) {
    return (
      <p className="text-xs text-zinc-600">
        Line history builds after a few odds syncs (~1 min each).
      </p>
    );
  }

  const values = points
    .map((p) =>
      p.moneyline_home != null ? impliedFromAmerican(p.moneyline_home) * 100 : null
    )
    .filter((v): v is number => v != null);

  if (values.length < 2) {
    return <p className="text-xs text-zinc-600">No moneyline history yet.</p>;
  }

  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const w = 280;
  const h = 64;
  const coords = values
    .map((v, i) => {
      const x = (i / (values.length - 1)) * w;
      const y = h - ((v - min) / range) * (h - 8) - 4;
      return `${x},${y}`;
    })
    .join(" ");

  const first = values[0];
  const last = values[values.length - 1];
  const delta = last - first;

  return (
    <div className="mt-3 rounded-lg border border-zinc-800 bg-zinc-950/80 p-3">
      <div className="mb-2 flex items-center justify-between text-xs">
        <span className="text-zinc-500">{homeTeam} implied win %</span>
        <span className={delta >= 0 ? "text-emerald-400" : "text-red-400"}>
          {delta >= 0 ? "+" : ""}
          {delta.toFixed(1)} pts
        </span>
      </div>
      <svg viewBox={`0 0 ${w} ${h}`} className="w-full h-16">
        <polyline
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          className="text-emerald-500/80"
          points={coords}
        />
      </svg>
      <div className="mt-1 flex justify-between text-[10px] text-zinc-600">
        <span>{first.toFixed(1)}%</span>
        <span>{last.toFixed(1)}%</span>
      </div>
    </div>
  );
}
