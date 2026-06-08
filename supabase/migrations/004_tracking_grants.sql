-- Allow backend (service_role) to manage parlay tracking + calibration

grant select, insert, update, delete on table public.saved_parlays to service_role;
grant select, insert, update, delete on table public.leg_outcomes to service_role;
grant select, insert, update, delete on table public.calibration_stats to service_role;
