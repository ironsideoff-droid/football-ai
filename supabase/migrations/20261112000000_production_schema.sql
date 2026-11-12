-- Football AI production schema.
--
-- The API connects with the database owner role (DATABASE_URL), which bypasses RLS. RLS is enabled on every table
-- with NO policies, so the Supabase REST/GraphQL API (anon and authenticated roles) can read or write nothing here.
-- Browsers only ever talk to Supabase Auth; all data goes through the API.

-- ---------------------------------------------------------------- immutable prediction history
create table if not exists public.predictions (
    match_id    bigint primary key,
    record      jsonb       not null,
    digest      text        not null,
    created_at  timestamptz not null default now()
);

create table if not exists public.results (
    match_id    bigint primary key,
    home        integer     not null check (home >= 0),
    away        integer     not null check (away >= 0),
    recorded_at timestamptz not null default now()
);

-- Full analysis shown to users, stored once per match (same moment as the prediction). Served to later users so a
-- fixture is analysed (and paid for in LLM calls) once, and everybody sees the analysis that is actually tracked.
create table if not exists public.analyses (
    match_id    bigint primary key,
    result      jsonb       not null,
    created_at  timestamptz not null default now()
);

create or replace function public.forbid_mutation() returns trigger
language plpgsql as $$
begin
    raise exception '% rows are append-only (% blocked)', tg_table_name, tg_op;
end;
$$;

drop trigger if exists predictions_immutable on public.predictions;
create trigger predictions_immutable before update or delete on public.predictions
    for each row execute function public.forbid_mutation();
drop trigger if exists results_immutable on public.results;
create trigger results_immutable before update or delete on public.results
    for each row execute function public.forbid_mutation();
drop trigger if exists analyses_immutable on public.analyses;
create trigger analyses_immutable before update or delete on public.analyses
    for each row execute function public.forbid_mutation();

-- ---------------------------------------------------------------- billing
create table if not exists public.subscriptions (
    user_id                uuid primary key references auth.users (id) on delete cascade,
    stripe_customer_id     text unique,
    stripe_subscription_id text,
    status                 text        not null default 'none',
    current_period_end     timestamptz,
    cancel_at_period_end   boolean     not null default false,
    stripe_event_created   bigint      not null default 0,  -- ignore older, out-of-order webhook deliveries
    updated_at             timestamptz not null default now()
);

-- Processed Stripe webhook events (idempotency).
create table if not exists public.stripe_events (
    id          text primary key,
    type        text        not null,
    received_at timestamptz not null default now()
);

-- Per-user daily analysis counter (fair-use limit).
create table if not exists public.analysis_usage (
    user_id uuid    not null references auth.users (id) on delete cascade,
    day     date    not null,
    count   integer not null default 0,
    primary key (user_id, day)
);

-- ---------------------------------------------------------------- lock the tables away from the public API
alter table public.predictions    enable row level security;
alter table public.results        enable row level security;
alter table public.analyses       enable row level security;
alter table public.subscriptions  enable row level security;
alter table public.stripe_events  enable row level security;
alter table public.analysis_usage enable row level security;

revoke all on public.predictions, public.results, public.analyses, public.subscriptions,
              public.stripe_events, public.analysis_usage from anon, authenticated;
