create extension if not exists pgcrypto;

create table if not exists public.site_settings (
  id smallint primary key default 1 check (id=1),
  display_name text not null default 'BOOM', headline text not null default 'Developer & Photographer', bio text,
  email text, phone text, location text, github_url text, instagram_url text, facebook_url text, linkedin_url text, line_url text, youtube_url text, booking_url text,
  contact_heading text not null default 'Let''s work together', contact_intro text, availability_text text, contact_button_text text not null default 'Send Message', show_contact_form boolean not null default true,
  hero_image_url text, site_url text, default_og_image_url text, analytics_id text, updated_at timestamptz not null default now()
);
insert into public.site_settings(id) values(1) on conflict(id) do nothing;

create table if not exists public.projects (
  id uuid primary key default gen_random_uuid(), title varchar(180) not null, slug varchar(200) unique not null,
  summary text, description text, cover_url text, demo_url text, source_url text,
  tech_stack jsonb not null default '[]'::jsonb, features jsonb not null default '[]'::jsonb, screenshots jsonb not null default '[]'::jsonb,
  role text, problem text, solution text,
  github_owner text, github_repo text, github_branch text, github_last_synced_at timestamptz,
  sort_order integer not null default 0, is_featured boolean not null default false, is_published boolean not null default false,
  seo_title text, seo_description text, created_at timestamptz not null default now(), updated_at timestamptz not null default now()
);

create table if not exists public.albums (
  id uuid primary key default gen_random_uuid(), title varchar(160) not null, slug varchar(180) unique not null,
  description text, category varchar(80) default 'Photography', event_date date,
  cover_drive_file_id text, drive_folder_id text, drive_folder_url text,
  is_published boolean not null default false, seo_title text, seo_description text,
  created_at timestamptz not null default now(), updated_at timestamptz not null default now()
);

create table if not exists public.album_photos (
  id uuid primary key default gen_random_uuid(), album_id uuid not null references public.albums(id) on delete cascade,
  drive_file_id text not null, file_name text, mime_type text, width integer, height integer,
  alt_text text, sort_order integer not null default 0, is_hidden boolean not null default false,
  created_at timestamptz not null default now(), unique(album_id,drive_file_id)
);

create table if not exists public.contact_messages (
  id uuid primary key default gen_random_uuid(), name varchar(120) not null, email varchar(180) not null,
  subject varchar(180), message text not null, is_read boolean not null default false, is_archived boolean not null default false,
  created_at timestamptz not null default now()
);

create table if not exists public.integration_tokens (
  id uuid primary key default gen_random_uuid(), provider text not null, owner_email text not null,
  encrypted_token text not null, created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
  unique(provider,owner_email)
);

create table if not exists public.audit_logs (
  id bigint generated always as identity primary key, actor_email text, action text not null, entity_type text, entity_id text,
  metadata jsonb not null default '{}'::jsonb, created_at timestamptz not null default now()
);

create index if not exists projects_pub_idx on public.projects(is_published,is_featured,sort_order);
create index if not exists albums_pub_idx on public.albums(is_published,created_at desc);
create index if not exists album_photos_album_idx on public.album_photos(album_id,sort_order);
create index if not exists messages_idx on public.contact_messages(is_archived,is_read,created_at desc);

alter table public.site_settings enable row level security;
alter table public.projects enable row level security;
alter table public.albums enable row level security;
alter table public.album_photos enable row level security;
alter table public.contact_messages enable row level security;
alter table public.integration_tokens enable row level security;
alter table public.audit_logs enable row level security;

-- Idempotent upgrades from V3
alter table public.site_settings add column if not exists site_url text;
alter table public.site_settings add column if not exists default_og_image_url text;
alter table public.site_settings add column if not exists analytics_id text;
alter table public.projects add column if not exists screenshots jsonb not null default '[]'::jsonb;
alter table public.projects add column if not exists role text;
alter table public.projects add column if not exists problem text;
alter table public.projects add column if not exists solution text;
alter table public.projects add column if not exists github_owner text;
alter table public.projects add column if not exists github_repo text;
alter table public.projects add column if not exists github_branch text;
alter table public.projects add column if not exists github_last_synced_at timestamptz;
alter table public.projects add column if not exists seo_title text;
alter table public.projects add column if not exists seo_description text;
alter table public.albums add column if not exists seo_title text;
alter table public.albums add column if not exists seo_description text;
alter table public.album_photos add column if not exists alt_text text;
alter table public.album_photos add column if not exists is_hidden boolean not null default false;
alter table public.contact_messages add column if not exists is_archived boolean not null default false;
