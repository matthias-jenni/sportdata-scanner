-- Apply in Supabase SQL Editor before uploading fight-card days with round selection.
-- NULL preserves legacy days whose round count is unknown.
ALTER TABLE public.event_days
    ADD COLUMN IF NOT EXISTS rounds integer;

ALTER TABLE public.event_days
    ADD CONSTRAINT event_days_rounds_valid
    CHECK (rounds IS NULL OR rounds IN (2, 3));