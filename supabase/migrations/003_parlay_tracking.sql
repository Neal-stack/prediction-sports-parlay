-- Parlay tracking + model calibration (anonymous sessions, no auth required)

create table if not exists saved_parlays (
  id uuid primary key default uuid_generate_v4(),
  session_id text not null,
  stake numeric(10,2) default 0,
  combined_american int not null,
  combined_implied_prob numeric(8,4),
  estimated_win_prob numeric(8,4),
  risk text not null check (risk in ('safe', 'balanced', 'bold')),
  same_game boolean default false,
  outcome text not null default 'pending'
    check (outcome in ('pending', 'win', 'loss', 'push')),
  legs jsonb not null,
  leg_outcomes jsonb not null default '[]',
  summary text,
  generated_at timestamptz not null,
  saved_at timestamptz default now(),
  settled_at timestamptz
);

create index if not exists idx_saved_parlays_session
  on saved_parlays(session_id, saved_at desc);

-- Individual leg results for calibration
create table if not exists leg_outcomes (
  id uuid primary key default uuid_generate_v4(),
  parlay_id uuid not null references saved_parlays(id) on delete cascade,
  session_id text not null,
  leg_index int not null,
  game_id text,
  sport text not null,
  market text not null,
  selection text not null,
  odds_american int not null,
  implied_prob numeric(8,4) not null,
  predicted_win_prob numeric(8,4) not null,
  confidence numeric(8,4),
  score numeric(10,4),
  risk text not null,
  outcome text not null check (outcome in ('win', 'loss', 'push')),
  recorded_at timestamptz default now(),
  unique(parlay_id, leg_index)
);

create index if not exists idx_leg_outcomes_calibration
  on leg_outcomes(sport, market, risk);

-- Aggregate calibration buckets (updated when legs are settled)
create table if not exists calibration_stats (
  id uuid primary key default uuid_generate_v4(),
  sport text not null,
  market text not null,
  risk text not null,
  prob_bucket text not null,
  predicted_avg numeric(8,4) not null default 0,
  actual_hit_rate numeric(8,4) not null default 0,
  sample_count int not null default 0,
  updated_at timestamptz default now(),
  unique(sport, market, risk, prob_bucket)
);
