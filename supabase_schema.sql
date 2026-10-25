-- Run in Supabase SQL Editor.
create table if not exists public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  plan text not null default 'free' check (plan in ('free','creator','pro')),
  stripe_customer_id text,
  subscription_status text not null default 'inactive',
  created_at timestamptz not null default now()
);
create table if not exists public.jobs (
  id text primary key,
  user_id uuid not null references auth.users(id) on delete cascade,
  title text not null,
  clips jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now()
);
alter table public.profiles enable row level security;
alter table public.jobs enable row level security;
create policy "users read own profile" on public.profiles for select using (auth.uid() = id);
create policy "users read own jobs" on public.jobs for select using (auth.uid() = user_id);
create policy "users insert own jobs" on public.jobs for insert with check (auth.uid() = user_id);
-- Server uses service-role key; never expose that key in frontend or GitHub.
create or replace function public.handle_new_user() returns trigger language plpgsql security definer set search_path=public as $$
begin insert into public.profiles(id, plan) values(new.id, 'free') on conflict (id) do nothing; return new; end; $$;
drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created after insert on auth.users for each row execute procedure public.handle_new_user();
