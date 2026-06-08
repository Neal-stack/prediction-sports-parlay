-- Prediction Parlay — initial schema

create extension if not exists "uuid-ossp";

-- Games from SharpAPI / odds feed
create table if not exists games (
  id text primary key,
  sport text not null,
  home_team text not null,
  away_team text not null,
  start_time timestamptz not null,
  venue text,
  is_outdoor boolean default false,
  updated_at timestamptz default now()
);

-- Odds snapshots (polled every 60s)
create table if not exists odds_snapshots (
  id uuid primary key default uuid_generate_v4(),
  game_id text not null references games(id) on delete cascade,
  book text not null default 'consensus',
  moneyline_home int,
  moneyline_away int,
  spread_home numeric(4,1),
  spread_away_odds int default -110,
  spread_home_odds int default -110,
  total numeric(4,1),
  over_odds int default -110,
  under_odds int default -110,
  captured_at timestamptz default now()
);

create index if not exists idx_odds_game_time on odds_snapshots(game_id, captured_at desc);

-- Context cache
create table if not exists injury_reports (
  id uuid primary key default uuid_generate_v4(),
  game_id text references games(id) on delete cascade,
  team text not null,
  player text not null,
  status text not null,
  fetched_at timestamptz default now()
);

create table if not exists team_news (
  id uuid primary key default uuid_generate_v4(),
  team text not null,
  headline text not null,
  url text,
  published_at timestamptz,
  fetched_at timestamptz default now()
);

create table if not exists weather_cache (
  game_id text primary key references games(id) on delete cascade,
  temp_f numeric(4,1),
  wind_mph numeric(4,1),
  conditions text,
  fetched_at timestamptz default now()
);

-- Auth + bankroll (Phase 5)
create table if not exists user_bets (
  id uuid primary key default uuid_generate_v4(),
  user_id uuid not null references auth.users(id) on delete cascade,
  game_id text references games(id),
  book text,
  market text not null,
  selection text not null,
  odds_american int not null,
  stake numeric(10,2),
  outcome text check (outcome in ('pending', 'win', 'loss', 'push')),
  placed_at timestamptz default now()
);

alter table user_bets enable row level security;

create policy "Users manage own bets"
  on user_bets for all
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

-- Public read for games/odds (no auth required for MVP dashboard)
alter table games enable row level security;
alter table odds_snapshots enable row level security;

create policy "Public read games" on games for select using (true);
create policy "Public read odds" on odds_snapshots for select using (true);
