-- CS PHOTO BY BOOM — Multi-slot Booking
-- Run once in Supabase SQL Editor. Safe to re-run.

create table if not exists public.booking_slots (
  id uuid primary key default gen_random_uuid(),
  booking_id uuid not null references public.bookings(id) on delete cascade,
  availability_id uuid not null references public.booking_availability(id) on delete restrict,
  is_reserved boolean not null default true,
  created_at timestamptz not null default now(),
  constraint booking_slots_unique_booking_slot unique(booking_id, availability_id)
);

-- One active reservation can own a slot at a time. This protects against
-- two customers selecting the same secondary slot at nearly the same moment.
create unique index if not exists booking_slots_one_reserved_per_availability_idx
on public.booking_slots(availability_id)
where is_reserved = true;

create index if not exists booking_slots_booking_idx
on public.booking_slots(booking_id);

create index if not exists booking_slots_reserved_idx
on public.booking_slots(is_reserved, availability_id);

-- Backfill every booking created before multi-slot support. Active bookings
-- keep their slot reserved; historical bookings are retained but released.
insert into public.booking_slots(booking_id, availability_id, is_reserved)
select b.id,
       b.availability_id,
       (b.status in ('pending','confirmed'))
from public.bookings b
where b.availability_id is not null
on conflict(booking_id, availability_id) do update
set is_reserved = excluded.is_reserved;

alter table public.booking_slots enable row level security;

notify pgrst, 'reload schema';
