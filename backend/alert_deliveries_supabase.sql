create table if not exists public.alert_deliveries (
  user_id text not null,
  source text not null,
  external_id text not null,
  created_at timestamptz not null default now(),
  primary key (user_id, source, external_id)
);

create index if not exists alert_deliveries_user_created_idx
  on public.alert_deliveries (user_id, created_at desc);
