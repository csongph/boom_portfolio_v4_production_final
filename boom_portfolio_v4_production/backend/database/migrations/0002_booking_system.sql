-- CS PHOTO BY BOOM — Booking System
-- Run once in Supabase SQL Editor. Safe to re-run.

create extension if not exists pgcrypto;

create table if not exists public.booking_slot_templates (
  id uuid primary key default gen_random_uuid(),
  label varchar(80) not null,
  start_time time not null,
  end_time time not null,
  sort_order integer not null default 0,
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint booking_slot_templates_time_check check (end_time > start_time),
  constraint booking_slot_templates_unique_time unique(start_time,end_time)
);

insert into public.booking_slot_templates(label,start_time,end_time,sort_order,is_active) values
  ('09:00 – 11:00','09:00','11:00',10,true),
  ('11:00 – 13:00','11:00','13:00',20,true),
  ('13:00 – 16:00','13:00','16:00',30,true),
  ('16:00 – 18:00','16:00','18:00',40,true),
  ('18:00 – 21:00','18:00','21:00',50,true)
on conflict(start_time,end_time) do update
set label=excluded.label, sort_order=excluded.sort_order, is_active=true, updated_at=now();

create table if not exists public.booking_availability (
  id uuid primary key default gen_random_uuid(),
  work_date date not null,
  slot_template_id uuid not null references public.booking_slot_templates(id) on delete restrict,
  is_open boolean not null default true,
  note text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint booking_availability_unique_slot unique(work_date,slot_template_id)
);

create table if not exists public.bookings (
  id uuid primary key default gen_random_uuid(),
  booking_code varchar(40) unique not null,
  availability_id uuid not null references public.booking_availability(id) on delete restrict,
  service_type varchar(60) not null,
  name varchar(120) not null,
  email varchar(180) not null,
  contact_method varchar(20) not null check(contact_method in ('instagram','line','phone','email')),
  contact_value varchar(180) not null,
  location varchar(300) not null,
  details text,
  status varchar(20) not null default 'pending' check(status in ('pending','confirmed','rejected','cancelled','completed')),
  manage_token_hash char(64) not null,
  admin_note text,
  cancellation_reason text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create unique index if not exists bookings_one_active_per_slot_idx
on public.bookings(availability_id)
where status in ('pending','confirmed');

create index if not exists booking_availability_date_open_idx on public.booking_availability(work_date,is_open);
create index if not exists bookings_status_date_idx on public.bookings(status,created_at desc);
create index if not exists bookings_email_idx on public.bookings(email,created_at desc);

create table if not exists public.booking_email_logs (
  id bigint generated always as identity primary key,
  booking_id uuid references public.bookings(id) on delete set null,
  recipient varchar(180) not null,
  subject varchar(240) not null,
  status varchar(20) not null check(status in ('sent','failed')),
  provider_message_id text,
  error_message text,
  created_at timestamptz not null default now()
);

create index if not exists booking_email_logs_booking_idx on public.booking_email_logs(booking_id,created_at desc);
create index if not exists booking_email_logs_status_idx on public.booking_email_logs(status,created_at desc);

create or replace function public.set_updated_at()
returns trigger language plpgsql set search_path=public as $$
begin new.updated_at=now(); return new; end; $$;

drop trigger if exists booking_slot_templates_updated_at on public.booking_slot_templates;
create trigger booking_slot_templates_updated_at before update on public.booking_slot_templates
for each row execute function public.set_updated_at();

drop trigger if exists booking_availability_updated_at on public.booking_availability;
create trigger booking_availability_updated_at before update on public.booking_availability
for each row execute function public.set_updated_at();

drop trigger if exists bookings_updated_at on public.bookings;
create trigger bookings_updated_at before update on public.bookings
for each row execute function public.set_updated_at();

alter table public.booking_slot_templates enable row level security;
alter table public.booking_availability enable row level security;
alter table public.bookings enable row level security;
alter table public.booking_email_logs enable row level security;

create or replace view public.booking_admin_view with (security_invoker = true) as
select a.id as availability_id,a.work_date,a.is_open,a.note as availability_note,
       s.id as slot_template_id,s.label as time_label,s.start_time,s.end_time,s.sort_order,
       b.id as booking_id,b.booking_code,b.service_type,b.name,b.email,b.contact_method,
       b.contact_value,b.location,b.details,b.status,b.admin_note,b.cancellation_reason,
       b.created_at as booking_created_at,b.updated_at as booking_updated_at
from public.booking_availability a
join public.booking_slot_templates s on s.id=a.slot_template_id
left join public.bookings b on b.availability_id=a.id and b.status in ('pending','confirmed')
order by a.work_date asc,s.sort_order asc;

notify pgrst, 'reload schema';
