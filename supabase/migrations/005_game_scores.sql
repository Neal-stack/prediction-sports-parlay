-- Final scores for auto-settlement

alter table games add column if not exists home_score int;
alter table games add column if not exists away_score int;
alter table games add column if not exists game_status text not null default 'scheduled'
  check (game_status in ('scheduled', 'live', 'final'));

create index if not exists idx_games_status_start
  on games(game_status, start_time desc);
