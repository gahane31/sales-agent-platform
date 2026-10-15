-- Model calls, prompt versions, eval scores, search cache, and spend ceilings.
-- The score does not read these tables.

create table if not exists public.llm_calls (
  id bigint generated always as identity primary key,
  created_at timestamptz not null default now(),
  run_id text,
  skill text not null,
  skill_version text not null,
  prompt_version text not null,
  model text not null,
  account_domain text,
  request_messages jsonb not null,
  response_text text,
  parsed_output jsonb,
  input_tokens int,
  output_tokens int,
  cost_usd numeric(10, 6),
  latency_ms int,
  validation_passed boolean,
  decision text not null,
  error text
);

create table if not exists public.prompt_registry (
  skill text not null,
  version text not null,
  path text,
  checksum text,
  is_active boolean not null default false,
  created_at timestamptz default now(),
  primary key (skill, version)
);

create table if not exists public.eval_runs (
  id bigint generated always as identity primary key,
  skill text not null,
  prompt_version text not null,
  prompt_checksum text,
  model text,
  metrics jsonb not null,
  weaknesses jsonb not null default '[]'::jsonb,
  notes text,
  run_at timestamptz default now()
);

alter table public.eval_runs
  add column if not exists prompt_checksum text,
  add column if not exists weaknesses jsonb not null default '[]'::jsonb,
  add column if not exists model text;

create table if not exists public.tavily_responses (
  account_domain text primary key,
  query text not null,
  response jsonb not null,
  fetched_at timestamptz not null default now()
);

create table if not exists public.llm_settings (
  key text primary key,
  value jsonb not null
);

insert into public.llm_settings (key, value) values
  ('default_model', '"gpt-4.1-mini"'),
  ('max_calls_per_run', '2500')
on conflict (key) do nothing;

insert into public.llm_settings (key, value) values
  ('max_cost_usd_per_run', '1'),
  ('max_cost_usd_per_day', '2'),
  ('max_cost_usd_per_month', '10')
on conflict (key) do update set value = excluded.value;

do $$
begin
  if exists (select 1 from pg_roles where rolname = 'app_role') then
    grant insert on public.llm_calls to app_role;
  end if;
end $$;
