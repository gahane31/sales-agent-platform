-- App-owned workflow. No ETL script reads these to decide a score,
-- and no ETL script writes them. account_feedback and account_workflow.status are
-- the labels a later ranking model would train on.

create table if not exists public.account_workflow (
  account_domain text primary key,
  status text not null default 'new',
  owner text,
  updated_at timestamptz not null default now()
);

create table if not exists public.account_notes (
  id bigint generated always as identity primary key,
  account_domain text not null,
  note text not null,
  created_at timestamptz not null default now()
);

create table if not exists public.account_feedback (
  id bigint generated always as identity primary key,
  account_domain text not null,
  feedback_type text not null,
  note text,
  created_at timestamptz not null default now()
);
