-- Run once in Supabase SQL Editor if the old rounds-column migration was applied.
-- Safe to run if it wasn't; local JSON days need no schema migration.
ALTER TABLE public.event_days DROP CONSTRAINT IF EXISTS event_days_rounds_valid;
ALTER TABLE public.event_days DROP COLUMN IF EXISTS rounds;