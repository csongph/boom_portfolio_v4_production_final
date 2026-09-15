-- CS BOOM Portfolio — retire the Booking system
-- Safe to run after booking has been removed from the application.
-- WARNING: this permanently deletes booking history and booking email logs.

drop table if exists public.booking_email_logs cascade;
drop table if exists public.booking_slots cascade;
drop table if exists public.bookings cascade;
drop table if exists public.booking_availability cascade;
drop table if exists public.booking_slot_templates cascade;

alter table public.site_settings drop column if exists booking_url;

notify pgrst, 'reload schema';
