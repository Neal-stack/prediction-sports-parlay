-- Player props (forward-looking).
-- Current build surfaces prop *anchors* computed on-demand from BallDontLie
-- season averages + the Gemini research pass; lines are model-derived because
-- free odds sources don't expose live prop lines. This table is where real
-- book prop lines will be persisted once a prop-odds source is added, so they
-- can become fully priced legs and be graded from box scores.

create table if not exists player_props (
  id uuid primary key default uuid_generate_v4(),
  game_id text not null references games(id) on delete cascade,
  sport text not null,
  player text not null,
  player_id text,
  stat text not null,                 -- 'points' | 'rebounds' | 'assists' | '3pm'
  line numeric(6,1) not null,         -- prop line (book or model-derived)
  over_odds int default -115,
  under_odds int default -115,
  player_avg numeric(6,2),            -- season average for the stat
  projected numeric(6,2),             -- model projection
  direction text check (direction in ('over', 'under')),
  confidence numeric(4,3),
  line_source text default 'model',   -- 'book' | 'model'
  captured_at timestamptz default now()
);

create index if not exists idx_player_props_game on player_props(game_id, captured_at desc);

-- Final box-score stat per player, used to grade prop legs at settlement.
create table if not exists player_box_scores (
  id uuid primary key default uuid_generate_v4(),
  game_id text not null references games(id) on delete cascade,
  player text not null,
  player_id text,
  points int,
  rebounds int,
  assists int,
  threes_made int,
  fetched_at timestamptz default now(),
  unique (game_id, player)
);

alter table player_props enable row level security;
alter table player_box_scores enable row level security;

create policy "Public read player_props" on player_props for select using (true);
create policy "Public read player_box_scores" on player_box_scores for select using (true);
