-- World Cup / soccer support: 3-way result needs a draw price.
-- Run this before deploying the soccer feature so odds sync keeps working.

alter table odds_snapshots
  add column if not exists draw_odds int;
