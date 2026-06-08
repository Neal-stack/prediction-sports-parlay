-- Allow backend (service_role) to write odds + context caches

grant usage on schema public to service_role;

grant select, insert, update, delete on table public.games to service_role;
grant select, insert, update, delete on table public.odds_snapshots to service_role;
grant select, insert, update, delete on table public.injury_reports to service_role;
grant select, insert, update, delete on table public.team_news to service_role;
grant select, insert, update, delete on table public.weather_cache to service_role;
