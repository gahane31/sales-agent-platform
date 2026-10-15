"""Every SQL statement the Python code runs.

Callers pass a connection and the values. The statement stays here, and the
function name is the table or view it touches. The files in pipeline/sql and
pipeline/etl are separate: the runner executes those scripts as whole jobs.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal

from psycopg.types.json import Json


def _json_dumps(value) -> str:
    def convert(obj):
        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")

    return json.dumps(value, default=convert)


def _execute(database_connection, statement: str, parameters: tuple = ()) -> None:
    database_connection.execute(statement, parameters).close()


def _fetchone(database_connection, statement: str, parameters: tuple = ()):
    cursor = database_connection.execute(statement, parameters)
    row = cursor.fetchone()
    cursor.close()
    return row


def _fetchall(database_connection, statement: str, parameters: tuple = ()):
    cursor = database_connection.execute(statement, parameters)
    rows = cursor.fetchall()
    cursor.close()
    return rows


def _rows(database_connection, statement: str, parameters: tuple = ()) -> list[dict]:
    cursor = database_connection.execute(statement, parameters)
    if cursor.description is None:
        cursor.close()
        return []
    column_names = [column.name for column in cursor.description]
    rows = [dict(zip(column_names, row)) for row in cursor.fetchall()]
    cursor.close()
    return rows


def set_statement_timeout(database_connection) -> None:
    _execute(database_connection, "set statement_timeout = '15min'")


def set_default_transaction_read_only(database_connection) -> None:
    _execute(database_connection, "set default_transaction_read_only = on")


def insert_etl_run(database_connection, script: str, mode: str, status: str = "running") -> int | None:
    row = _fetchone(
        database_connection,
        """
        insert into public.etl_runs (script, mode, status)
        values (%s, %s, %s)
        returning id
        """,
        (script, mode, status),
    )
    return None if row is None else int(row[0])


def update_etl_run(
    database_connection,
    etl_run_id: int,
    status: str,
    rows_affected: int,
    error: str | None,
) -> None:
    _execute(
        database_connection,
        """
        update public.etl_runs
        set status = %s, rows_affected = %s, finished_at = now(), error = %s
        where id = %s
        """,
        (status, rows_affected, error, etl_run_id),
    )


def insert_account_workflow(database_connection, account_domain: str, status: str, owner: str | None) -> None:
    _execute(
        database_connection,
        """
        insert into public.account_workflow (account_domain, status, owner)
        values (%s, %s, %s)
        on conflict (account_domain) do update
          set status = excluded.status,
              owner = coalesce(excluded.owner, public.account_workflow.owner),
              updated_at = now()
        """,
        (account_domain, status, owner),
    )


def insert_account_note(database_connection, account_domain: str, note: str) -> None:
    _execute(
        database_connection,
        "insert into public.account_notes (account_domain, note) values (%s, %s)",
        (account_domain, note),
    )


def insert_account_feedback(
    database_connection,
    account_domain: str,
    feedback_type: str,
    note: str | None,
) -> None:
    _execute(
        database_connection,
        """
        insert into public.account_feedback (account_domain, feedback_type, note)
        values (%s, %s, %s)
        """,
        (account_domain, feedback_type, note),
    )


def select_account_score_totals(database_connection) -> dict:
    rows = _rows(
        database_connection,
        """
        select
          (select count(*) from public.account_scores) as accounts,
          (select count(*) from public.account_scores where tier = 'A') as tier_a,
          (select count(*) from public.account_scores where tier = 'B') as tier_b,
          (select count(*) from public.account_scores where tier = 'C') as tier_c,
          (select count(*) from public.signals where signal_type = 'kev_exploited') as kev_accounts,
          (select count(*) from public.account_scores s
            where s.tier in ('A', 'B')
              and s.computed_at > coalesce((
                select finished_at from public.etl_runs
                where script like '%%account_scores%%' and status = 'ok'
                order by finished_at desc offset 1 limit 1
              ), '-infinity')) as whats_new
        """,
    )
    return rows[0]


def select_domain_industries(database_connection) -> list[str]:
    return [
        row["industry"]
        for row in _rows(
            database_connection,
            "select distinct industry from public.domains where industry is not null order by 1",
        )
    ]


def select_domain_countries(database_connection) -> list[str]:
    return [
        row["country"]
        for row in _rows(
            database_connection,
            "select distinct country from public.domains where country is not null order by 1",
        )
    ]


def select_priority_queue(
    database_connection,
    *,
    tiers: list[str],
    minimum_score: int,
    workflow_statuses: list[str],
    search: str,
    industry: str,
    country: str,
    business_type: str,
    kev_only: bool,
    signal: str,
    owner: str = "Any",
    row_limit: int | None = 300,
) -> list[dict]:
    search_text = search.strip()
    signal_text = signal.strip()
    limit_clause = "limit %s" if row_limit is not None else ""
    parameters = [
        tiers,
        minimum_score,
        workflow_statuses,
        search_text,
        f"%{search_text}%",
        f"%{search_text}%",
        industry,
        industry,
        country,
        country,
        business_type,
        business_type,
        kev_only,
        owner,
        owner,
        owner,
        signal_text,
        f"%{signal_text}%",
        f"%{signal_text}%",
    ]
    if row_limit is not None:
        parameters.append(row_limit)
    return _rows(
        database_connection,
        f"""
        select account_domain, display_name, top_reason, composite_score, tier, industry, country,
               business_type, workflow_status, has_kev, need_score,
               fit_score, timing_score, reach_score, first_seen, owner,
               count(*) over () as match_count
        from public.v_priority_queue
        where tier = any(%s)
          and composite_score >= %s
          and workflow_status = any(%s)
          and (%s = '' or display_name ilike %s or account_domain ilike %s)
          and (%s = 'Any' or industry = %s)
          and (%s = 'Any' or country = %s)
          and (%s = 'Any' or business_type = %s)
          and (%s = false or has_kev)
          and (%s = 'Any' or (%s = 'Unassigned' and coalesce(owner, '') = '') or owner = %s)
          and (
            %s = '' or exists (
              select 1
              from public.signals s
              join public.signal_catalog c using (signal_type)
              where s.account_domain = v_priority_queue.account_domain
                and (
                  s.signal_type ilike %s
                  or coalesce(c.human_label, '') ilike %s
                )
            )
          )
        order by composite_score desc, has_kev desc, need_score desc
        {limit_clause}
        """,
        tuple(parameters),
    )


def select_account_page(database_connection, account_domain: str) -> list[dict]:
    return _rows(
        database_connection,
        """
        select coalesce(d.company_name, d.domain) as display_name, d.industry, d.country,
               d.business_type, d.enrich_source, s.need_score, s.fit_score, s.timing_score,
               s.reach_score, s.composite, s.tier, s.company_overview_name, s.company_overview,
               s.signal_insights, s.outreach,
               coalesce(w.status, 'new') as status,
               d.first_seen, d.last_seen, coalesce(w.owner, '') as owner, d.ip_count, d.scan_count, d.domain
        from public.domains d
        left join public.account_scores s on s.account_domain = d.domain
        left join public.account_workflow w on w.account_domain = d.domain
        where d.domain = %s
        """,
        (account_domain,),
    )


def select_account_detail(database_connection, account_domain: str) -> list[dict]:
    return _rows(
        database_connection,
        """
        select signal_type, need_weight, human_label, human_meaning, description, scan_count, evidence
        from public.v_account_detail
        where account_domain = %s
        order by need_weight desc, signal_type
        """,
        (account_domain,),
    )


def select_account_technology(database_connection, account_domain: str) -> list[dict]:
    return _rows(
        database_connection,
        """
        select c.component,
               c.categories,
               count(distinct c.scan_id) as "Seen on scans",
               string_agg(distinct nullif(btrim(p.version), ''), ', ' order by nullif(btrim(p.version), '')) as versions
        from public.v_exposed_scans e
        join public.scan_components c on c.scan_id = e.scan_id
        left join public.scan_cpes p
          on p.scan_id = c.scan_id
         and (
           lower(c.component) = lower(p.product)
           or lower(c.component) = lower(replace(p.product, '_', ' '))
           or lower(replace(c.component, ' ', '')) = lower(replace(p.product, '_', ''))
           or lower(c.component) = lower(p.vendor || ' ' || replace(p.product, '_', ' '))
         )
        where e.account_domain = %s
        group by c.component, c.categories
        order by
          bool_or(
            nullif(btrim(p.version), '') is not null
            and exists (
              select 1 from unnest(c.categories) as category
              where category ~* 'server'
            )
          ) desc,
          count(distinct c.scan_id) desc,
          c.component
        limit 40
        """,
        (account_domain,),
    )


def select_exposed_ports(database_connection, account_domain: str) -> list[dict]:
    return _rows(
        database_connection,
        """
        select coalesce(p.category, 'other') as category, r.port, count(distinct r.ip) as hosts
        from public.v_exposed_scans e
        join public.scan_records r on r.scan_id = e.scan_id
        left join public.risky_ports p on p.port = r.port
        where e.account_domain = %s
        group by 1, 2
        order by 1, 2
        """,
        (account_domain,),
    )


def select_account_notes(database_connection, account_domain: str) -> list[dict]:
    return _rows(
        database_connection,
        """
        select note, created_at
        from public.account_notes
        where account_domain = %s
        order by created_at desc
        """,
        (account_domain,),
    )


def select_accounts_for_company_overview(database_connection, limit: int, account_domain: str | None = None):
    if account_domain:
        return _rows(
            database_connection,
            """
            select s.account_domain, d.company_name, s.company_overview
            from public.account_scores s
            join public.domains d on d.domain = s.account_domain
            where s.account_domain = %s
            """,
            (account_domain,),
        )
    return _rows(
        database_connection,
        """
        select s.account_domain, d.company_name, s.company_overview
        from public.account_scores s
        join public.domains d on d.domain = s.account_domain
        where s.tier = any(%s)
        order by s.composite desc, s.account_domain
        limit %s
        """,
        (["A", "B"], limit),
    )


def select_tavily_response(database_connection, account_domain: str):
    rows = _rows(
        database_connection,
        """
        select query, response
        from public.tavily_responses
        where account_domain = %s
        """,
        (account_domain,),
    )
    return rows[0] if rows else None


def save_tavily_response(database_connection, account_domain: str, query: str, response: dict) -> None:
    _execute(
        database_connection,
        """
        insert into public.tavily_responses (account_domain, query, response, fetched_at)
        values (%s, %s, %s, now())
        on conflict (account_domain) do update set
          query = excluded.query,
          response = excluded.response,
          fetched_at = now()
        """,
        (account_domain, query, Json(response, dumps=_json_dumps)),
    )


def select_accounts_for_signal_insights(database_connection, limit: int, account_domain: str | None = None):
    if account_domain:
        return _rows(
            database_connection,
            """
            select s.account_domain,
                   coalesce(nullif(s.company_overview_name, ''), d.company_name, s.account_domain) as company_name,
                   s.company_overview,
                   s.signal_insights
            from public.account_scores s
            join public.domains d on d.domain = s.account_domain
            where s.account_domain = %s
            """,
            (account_domain,),
        )
    return _rows(
        database_connection,
        """
        select s.account_domain,
               coalesce(nullif(s.company_overview_name, ''), d.company_name, s.account_domain) as company_name,
               s.company_overview,
               s.signal_insights
        from public.account_scores s
        join public.domains d on d.domain = s.account_domain
        where s.tier = any(%s)
        order by s.composite desc, s.account_domain
        limit %s
        """,
        (["A", "B"], limit),
    )


def select_signals_for_insight(database_connection, account_domain: str):
    return _rows(
        database_connection,
        """
        select s.signal_type, s.evidence, s.scan_count,
               c.product_angle, c.description, c.need_weight, c.timing_weight,
               coalesce(nullif(c.human_label, ''), s.signal_type) as human_label,
               coalesce(c.human_meaning, c.description) as human_meaning
        from public.signals s
        join public.signal_catalog c using (signal_type)
        where s.account_domain = %s
        order by c.need_weight desc nulls last, s.signal_type
        """,
        (account_domain,),
    )


def save_signal_insights(database_connection, account_domain: str, insights: str) -> None:
    _execute(
        database_connection,
        """
        update public.account_scores
        set signal_insights = %s
        where account_domain = %s
        """,
        (insights, account_domain),
    )


def select_accounts_for_outreach(database_connection, limit: int, account_domain: str | None = None):
    if account_domain:
        return _rows(
            database_connection,
            """
            select s.account_domain,
                   coalesce(nullif(s.company_overview_name, ''), d.company_name, s.account_domain) as company_name,
                   s.company_overview,
                   s.outreach
            from public.account_scores s
            join public.domains d on d.domain = s.account_domain
            where s.account_domain = %s
            """,
            (account_domain,),
        )
    return _rows(
        database_connection,
        """
        select s.account_domain,
               coalesce(nullif(s.company_overview_name, ''), d.company_name, s.account_domain) as company_name,
               s.company_overview,
               s.outreach
        from public.account_scores s
        join public.domains d on d.domain = s.account_domain
        where s.tier = any(%s)
        order by s.composite desc, s.account_domain
        limit %s
        """,
        (["A", "B"], limit),
    )


def select_signal_catalog(database_connection):
    return _rows(
        database_connection,
        """
        select signal_type, human_label, human_meaning, need_weight, timing_weight,
               confidence, product_angle, description
        from public.signal_catalog
        order by need_weight desc nulls last, signal_type
        """,
    )


def save_outreach(database_connection, account_domain: str, outreach: str) -> None:
    _execute(
        database_connection,
        """
        update public.account_scores
        set outreach = %s
        where account_domain = %s
        """,
        (outreach, account_domain),
    )


def save_company_overview(database_connection, account_domain: str, company_name: str | None, overview: str) -> None:
    _execute(
        database_connection,
        """
        update public.account_scores
        set company_overview_name = %s,
            company_overview = %s
        where account_domain = %s
        """,
        (company_name, overview, account_domain),
    )


def select_llm_setting(database_connection, key: str):
    return _fetchone(
        database_connection,
        "select value from public.llm_settings where key = %s",
        (key,),
    )


def select_llm_call_spend(database_connection, run_id: str):
    return _fetchone(
        database_connection,
        """
        select count(*), coalesce(sum(cost_usd), 0)
        from public.llm_calls
        where run_id = %s and decision <> 'budget_exceeded'
        """,
        (run_id,),
    )


def select_llm_period_spend(database_connection, start):
    return _fetchone(
        database_connection,
        """
        select coalesce(sum(cost_usd), 0)
        from public.llm_calls
        where skill = any(%s)
          and decision <> 'budget_exceeded'
          and created_at >= %s
        """,
        (["company-overview", "signal-insights", "outreach"], start),
    )


def insert_llm_call_accepted(
    database_connection,
    *,
    run_id,
    skill,
    skill_version,
    prompt_version,
    model,
    account_domain,
    request_messages,
    response_text,
    parsed_output,
    input_tokens,
    output_tokens,
    cost_usd,
    latency_ms,
) -> int:
    row = _fetchone(
        database_connection,
        """
        insert into public.llm_calls (
          run_id, skill, skill_version, prompt_version, model, account_domain,
          request_messages, response_text, parsed_output, input_tokens, output_tokens,
          cost_usd, latency_ms, decision
        ) values (
          %s, %s, %s, %s, %s, %s,
          %s, %s, %s, %s, %s,
          %s, %s, 'accepted'
        )
        returning id
        """,
        (
            run_id,
            skill,
            skill_version,
            prompt_version,
            model,
            account_domain,
            Json(request_messages),
            response_text,
            Json(parsed_output) if parsed_output is not None else None,
            input_tokens,
            output_tokens,
            cost_usd,
            latency_ms,
        ),
    )
    return int(row[0])


def update_llm_call_validation(database_connection, call_id: int, passed: bool) -> None:
    _execute(
        database_connection,
        "update public.llm_calls set validation_passed = %s where id = %s",
        (passed, call_id),
    )


def insert_llm_call_error(
    database_connection,
    *,
    run_id,
    skill,
    skill_version,
    prompt_version,
    model,
    account_domain,
    request_messages,
    latency_ms,
    error,
) -> None:
    _execute(
        database_connection,
        """
        insert into public.llm_calls (
          run_id, skill, skill_version, prompt_version, model, account_domain,
          request_messages, latency_ms, cost_usd, decision, error
        ) values (
          %s, %s, %s, %s, %s, %s,
          %s, %s, 0, 'error', %s
        )
        """,
        (
            run_id,
            skill,
            skill_version,
            prompt_version,
            model,
            account_domain,
            Json(request_messages),
            latency_ms,
            str(error)[:500],
        ),
    )


def insert_llm_call_budget_exceeded(
    database_connection,
    *,
    run_id,
    skill,
    model,
    account_domain,
    error,
) -> None:
    _execute(
        database_connection,
        """
        insert into public.llm_calls (
          run_id, skill, skill_version, prompt_version, model, account_domain,
          request_messages, cost_usd, decision, error
        ) values (
          %s, %s, 'v1', 'v1', %s, %s,
          %s, 0, 'budget_exceeded', %s
        )
        """,
        (run_id, skill, model, account_domain, Json([]), error),
    )


def insert_llm_call_ai_disabled(
    database_connection,
    *,
    run_id,
    skill,
    skill_version,
    prompt_version,
    model,
    account_domain,
    error,
) -> None:
    _execute(
        database_connection,
        """
        insert into public.llm_calls (
          run_id, skill, skill_version, prompt_version, model, account_domain,
          request_messages, cost_usd, decision, error
        ) values (
          %s, %s, %s, %s, %s, %s,
          %s, 0, 'ai_disabled', %s
        )
        """,
        (
            run_id,
            skill,
            skill_version,
            prompt_version,
            model,
            account_domain,
            Json([]),
            error,
        ),
    )


def insert_prompt_registry(
    database_connection,
    skill: str,
    version: str,
    path: str,
    checksum: str,
) -> None:
    _execute(
        database_connection,
        """
        insert into public.prompt_registry (skill, version, path, checksum, is_active)
        values (%s, %s, %s, %s, false)
        on conflict (skill, version) do update
          set path = excluded.path, checksum = excluded.checksum
        """,
        (skill, version, path, checksum),
    )


def activate_newest_prompt(database_connection, skill: str) -> None:
    _execute(
        database_connection,
        "update public.prompt_registry set is_active = false where skill = %s",
        (skill,),
    )
    _execute(
        database_connection,
        """
        update public.prompt_registry
        set is_active = true
        where skill = %s
          and version = (
            select version
            from public.prompt_registry
            where skill = %s
            order by substring(version from '[0-9]+')::int desc nulls last, version desc
            limit 1
          )
        """,
        (skill, skill),
    )


def select_active_prompt_version(database_connection, skill: str):
    return _fetchone(
        database_connection,
        """
        select version
        from public.prompt_registry
        where skill = %s and is_active
        order by substring(version from '[0-9]+')::int desc nulls last, version desc
        limit 1
        """,
        (skill,),
    )


def activate_prompt_registry_if_none_active(database_connection, skill: str, version: str) -> None:
    _execute(
        database_connection,
        """
        update public.prompt_registry
        set is_active = true
        where skill = %s and version = %s
          and not exists (
            select 1 from public.prompt_registry
            where skill = %s and is_active
          )
        """,
        (skill, version, skill),
    )


def select_prompt_registry_path(database_connection, skill: str, version: str):
    return _fetchone(
        database_connection,
        "select path from public.prompt_registry where skill = %s and version = %s",
        (skill, version),
    )


def select_active_prompt_registry_path(database_connection, skill: str):
    return _fetchone(
        database_connection,
        """
        select path from public.prompt_registry
        where skill = %s and is_active
        order by substring(version from '[0-9]+')::int desc nulls last, version desc
        limit 1
        """,
        (skill,),
    )


def select_account_evidence_context(database_connection, account_domains: list[str]) -> list[dict]:
    return _rows(
        database_connection,
        """
        select
          e.account_domain,
          max(r.scanned_at) as scan_date,
          count(distinct r.ip) as hosts,
          count(distinct e.scan_id) as open_services,
          count(distinct c.component) as distinct_software,
          coalesce(
            min((s.expires_at at time zone 'UTC')::date - (r.scanned_at at time zone 'UTC')::date)
              filter (where s.expires_at >= r.scanned_at),
            max((s.expires_at at time zone 'UTC')::date - (r.scanned_at at time zone 'UTC')::date)
              filter (where s.expires_at < r.scanned_at)
          ) as days_until_expiry
        from public.v_exposed_scans e
        join public.scan_records r on r.scan_id = e.scan_id
        left join public.scan_components c on c.scan_id = e.scan_id
        left join public.scan_ssl s on s.scan_id = e.scan_id and s.expires_at is not null
        where e.account_domain = any(%s)
        group by e.account_domain
        """,
        (account_domains,),
    )


def select_system_cost(database_connection) -> dict:
    row = _fetchone(
        database_connection,
        """
        select
          coalesce(sum(cost_usd) filter (
            where skill in ('company-overview', 'signal-insights', 'outreach')
          ), 0) as brief_cost,
          count(distinct account_domain) filter (
            where skill in ('company-overview', 'signal-insights', 'outreach')
              and account_domain is not null
          ) as briefed_accounts,
          coalesce(sum(cost_usd) filter (
            where skill in ('company-overview', 'signal-insights', 'outreach')
              and decision <> 'budget_exceeded'
              and created_at >= date_trunc('day', now() at time zone 'utc') at time zone 'utc'
          ), 0) as day_spent,
          coalesce(sum(cost_usd) filter (
            where skill in ('company-overview', 'signal-insights', 'outreach')
              and decision <> 'budget_exceeded'
              and created_at >= date_trunc('month', now() at time zone 'utc') at time zone 'utc'
          ), 0) as month_spent,
          coalesce((
            select nullif(trim(both '"' from value::text), '')::numeric
            from public.llm_settings
            where key = 'max_cost_usd_per_run'
          ), 1) as run_ceiling,
          coalesce((
            select nullif(trim(both '"' from value::text), '')::numeric
            from public.llm_settings
            where key = 'max_cost_usd_per_day'
          ), 2) as day_ceiling,
          coalesce((
            select nullif(trim(both '"' from value::text), '')::numeric
            from public.llm_settings
            where key = 'max_cost_usd_per_month'
          ), 10) as month_ceiling
        from public.llm_calls
        where skill is distinct from 'budget-proof'
        """,
    )
    return {
        "brief_cost": float(row[0] or 0),
        "briefed_accounts": int(row[1] or 0),
        "day_spent": float(row[2] or 0),
        "month_spent": float(row[3] or 0),
        "run_ceiling": float(row[4] or 1),
        "day_ceiling": float(row[5] or 2),
        "month_ceiling": float(row[6] or 10),
    }


def select_controlled_proofs(database_connection) -> list[dict]:
    return _rows(
        database_connection,
        """
        select decision, count(*) as calls, coalesce(sum(cost_usd), 0) as cost_usd
        from public.llm_calls
        where skill = 'budget-proof'
        group by decision
        order by calls desc, decision
        """,
    )


def select_llm_call_summary(database_connection) -> list[dict]:
    return _rows(
        database_connection,
        """
        select
          skill,
          count(*) as calls,
          coalesce(sum(cost_usd), 0) as cost_usd,
          percentile_cont(0.5) within group (order by latency_ms) as latency_p50_ms,
          percentile_cont(0.95) within group (order by latency_ms) as latency_p95_ms
        from public.llm_calls
        group by skill
        order by cost_usd desc, skill
        """,
    )


def select_llm_call_decisions(database_connection) -> list[dict]:
    return _rows(
        database_connection,
        """
        select decision, count(*) as calls, coalesce(sum(cost_usd), 0) as cost_usd
        from public.llm_calls
        group by decision
        order by calls desc, decision
        """,
    )


def insert_eval_run(
    database_connection,
    skill: str,
    prompt_version: str,
    prompt_checksum: str,
    model: str,
    metrics: dict,
    weaknesses: list,
) -> None:
    _execute(
        database_connection,
        """
        insert into public.eval_runs (skill, prompt_version, prompt_checksum, model, metrics, weaknesses)
        values (%s, %s, %s, %s, %s, %s)
        """,
        (skill, prompt_version, prompt_checksum, model, Json(metrics), Json(weaknesses)),
    )


def select_previous_eval_run(database_connection, skill: str, prompt_checksum: str, model: str):
    rows = _rows(
        database_connection,
        """
        select prompt_version, prompt_checksum, model, metrics, weaknesses
        from public.eval_runs
        where skill = %s
          and (
            (prompt_checksum is not null and prompt_checksum <> %s)
            or (model is not null and model <> %s)
          )
        order by run_at desc
        limit 1
        """,
        (skill, prompt_checksum, model),
    )
    return rows[0] if rows else None


def select_daily_model_spend(database_connection, start_day) -> list[dict]:
    return _rows(
        database_connection,
        """
        with days as (
          select generate_series(
            %s::date,
            (now() at time zone 'utc')::date,
            interval '1 day'
          )::date as day
        ),
        series as (
          select unnest(array['company-overview', 'signal-insights', 'outreach', 'eval']) as series
        ),
        spent as (
          select (created_at at time zone 'utc')::date as day,
                 case when run_id like 'eval-%%' then 'eval' else skill end as series,
                 coalesce(sum(cost_usd), 0) as cost_usd
          from public.llm_calls
          where skill in ('company-overview', 'signal-insights', 'outreach')
            and decision <> 'budget_exceeded'
            and created_at >= (%s::date::timestamp at time zone 'utc')
          group by 1, 2
        )
        select to_char(d.day, 'YYYY-MM-DD') as day,
               srs.series as skill,
               coalesce(spent.cost_usd, 0)::float as cost_usd
        from days d
        cross join series srs
        left join spent on spent.day = d.day and spent.series = srs.series
        order by d.day, srs.series
        """,
        (start_day, start_day),
    )


def select_recent_model_runs(database_connection) -> list[dict]:
    return _rows(
        database_connection,
        """
        select run_id,
               min(created_at) as started,
               string_agg(distinct skill, ', ' order by skill) as agents,
               count(*) filter (where decision <> 'budget_exceeded') as calls,
               coalesce(sum(cost_usd) filter (where decision <> 'budget_exceeded'), 0)::float as cost_usd,
               count(*) filter (where validation_passed is true) as kept,
               count(*) filter (where validation_passed is false) as rejected,
               max(error) filter (where decision = 'budget_exceeded') as stopped
        from public.llm_calls
        where skill in ('company-overview', 'signal-insights', 'outreach')
        group by run_id
        order by min(created_at) desc
        limit 10
        """,
    )


def select_last_pipeline_run(database_connection) -> dict | None:
    rows = _rows(
        database_connection,
        """
        with latest as (
          select run_id
          from public.llm_calls
          where run_id like 'pipeline-%%'
            and skill in ('company-overview', 'signal-insights', 'outreach')
          order by created_at desc
          limit 1
        )
        select latest.run_id,
               coalesce(sum(c.cost_usd) filter (where c.decision <> 'budget_exceeded'), 0)::float as cost_usd,
               count(*) filter (where c.decision <> 'budget_exceeded') as calls
        from latest
        left join public.llm_calls c on c.run_id = latest.run_id
        group by latest.run_id
        """,
    )
    return rows[0] if rows else None


def select_full_account_cost(database_connection) -> tuple[int, float]:
    row = _fetchone(
        database_connection,
        """
        with per_account as (
          select account_domain,
                 count(distinct skill) as skills,
                 coalesce(sum(cost_usd), 0) as cost_usd
          from public.llm_calls
          where skill in ('company-overview', 'signal-insights', 'outreach')
            and decision = 'accepted'
            and account_domain is not null
          group by account_domain
        )
        select count(*) filter (where skills = 3),
               coalesce(sum(cost_usd) filter (where skills = 3), 0)
        from per_account
        """,
    )
    return int(row[0] or 0), float(row[1] or 0)


def select_output_coverage(database_connection) -> dict:
    row = _fetchone(
        database_connection,
        """
        with top as (
          select company_overview, signal_insights, outreach
          from public.account_scores
          where tier in ('A', 'B')
          order by composite desc, account_domain
          limit 20
        )
        select count(*) as total,
               count(*) filter (where nullif(btrim(company_overview), '') is not null) as overviews,
               count(*) filter (where nullif(btrim(signal_insights), '') is not null) as insights,
               count(*) filter (where nullif(btrim(outreach), '') is not null) as outreach
        from top
        """,
    )
    return {
        "total": int(row[0] or 0),
        "overviews": int(row[1] or 0),
        "insights": int(row[2] or 0),
        "outreach": int(row[3] or 0),
    }


def select_validation_rates(database_connection) -> list[dict]:
    return _rows(
        database_connection,
        """
        select skill,
               count(*) filter (where validation_passed is not null) as checked,
               count(*) filter (where validation_passed is false) as rejected
        from public.llm_calls
        where skill in ('company-overview', 'signal-insights', 'outreach')
          and decision = 'accepted'
        group by skill
        order by skill
        """,
    )


def select_eval_skills(database_connection) -> list[str]:
    rows = _rows(
        database_connection,
        """
        select skill
        from public.eval_runs
        group by skill
        order by max(run_at) desc
        """,
    )
    return [row["skill"] for row in rows]


def select_latest_eval_run(database_connection, skill: str) -> dict | None:
    rows = _rows(
        database_connection,
        """
        select prompt_version, prompt_checksum, model, metrics, weaknesses, run_at
        from public.eval_runs
        where skill = %s
        order by run_at desc
        limit 1
        """,
        (skill,),
    )
    return rows[0] if rows else None


def select_active_prompts(database_connection) -> list[dict]:
    return _rows(
        database_connection,
        """
        select skill, version, checksum
        from public.prompt_registry
        where is_active
        order by skill
        """,
    )


