begin;

-- Keep album deletion atomic and safe for databases created before the current schema.
alter table public.album_photos drop constraint if exists album_photos_album_id_fkey;
alter table public.album_photos add constraint album_photos_album_id_fkey foreign key (album_id) references public.albums(id) on delete cascade;
alter table public.photo_likes drop constraint if exists photo_likes_photo_id_fkey;
alter table public.photo_likes add constraint photo_likes_photo_id_fkey foreign key (photo_id) references public.album_photos(id) on delete cascade;
alter table public.photo_events drop constraint if exists photo_events_album_id_fkey;
alter table public.photo_events add constraint photo_events_album_id_fkey foreign key (album_id) references public.albums(id) on delete cascade;
alter table public.photo_events drop constraint if exists photo_events_photo_id_fkey;
alter table public.photo_events add constraint photo_events_photo_id_fkey foreign key (photo_id) references public.album_photos(id) on delete cascade;

create index if not exists albums_category_pub_idx on public.albums(category,is_published,created_at desc);
create index if not exists photo_likes_created_idx on public.photo_likes(created_at desc);
create index if not exists photo_events_created_idx on public.photo_events(created_at desc);
create index if not exists photo_events_album_created_idx on public.photo_events(album_id,created_at desc);

create or replace function public.reorder_album_photos(p_album_id uuid,p_photo_ids uuid[])
returns void language plpgsql set search_path=public as $$
declare updated_count integer;
begin
  if cardinality(p_photo_ids) <> (select count(*) from public.album_photos where album_id=p_album_id) then
    raise exception 'Photo order must contain every album photo exactly once';
  end if;
  update public.album_photos p
  set sort_order=o.position-1
  from unnest(p_photo_ids) with ordinality as o(photo_id,position)
  where p.id=o.photo_id and p.album_id=p_album_id;
  get diagnostics updated_count=row_count;
  if updated_count <> cardinality(p_photo_ids) then raise exception 'Invalid or duplicate photo id'; end if;
end;
$$;

create or replace function public.update_album_photo_metadata(p_album_id uuid,p_items jsonb)
returns integer language plpgsql set search_path=public as $$
declare updated_count integer;
begin
  update public.album_photos p
  set alt_text=left(trim(i.alt_text),300)
  from jsonb_to_recordset(p_items) as i(id uuid,alt_text text)
  where p.id=i.id and p.album_id=p_album_id and trim(coalesce(i.alt_text,''))<>'';
  get diagnostics updated_count=row_count;
  return updated_count;
end;
$$;

create or replace function public.dashboard_counts()
returns jsonb language sql stable set search_path=public as $$
  select jsonb_build_object(
    'projects',(select count(*) from public.projects),
    'published_projects',(select count(*) from public.projects where is_published),
    'albums',(select count(*) from public.albums),
    'photos',(select count(*) from public.album_photos),
    'published_albums',(select count(*) from public.albums where is_published)
  );
$$;

commit;
