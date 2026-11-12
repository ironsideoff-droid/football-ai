create table public.subscriptions (
  user_id uuid primary key references auth.users(id) on delete cascade,
  stripe_customer_id text unique,
  stripe_subscription_id text unique,
  status text not null default 'none',
  current_period_end timestamptz,
  cancel_at_period_end boolean not null default false,
  updated_at timestamptz not null default now()
);
alter table public.subscriptions enable row level security;
create policy "users read own subscription" on public.subscriptions
  for select to authenticated using ((select auth.uid()) = user_id);

create table public.stripe_events (
  id text primary key,
  type text not null,
  received_at timestamptz not null default now()
);
alter table public.stripe_events enable row level security;
