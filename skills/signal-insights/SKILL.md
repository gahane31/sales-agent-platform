---
name: signal-insights
description: Turn an account's stored scan signals into salesperson cards on the Signals tab. Use when signal insights are missing, or when rewriting them for one domain.
---

# Signal insights

This file is not sent to the model. The model reads `pipeline/ai/prompts/signal-insights/v1.md`. The program that loads the signals, calls the model, checks the answer, and saves it is `pipeline/jobs/generate_signal_insights.py`.

## When to run

Run this when an account already has scan signals and the Signals tab is empty.

Do not run it to score the account. Need, fit, timing, and tier are SQL.

Do not run it to write the company profile or the outreach email. Those are the company-overview and outreach jobs.

If `AI_ENABLED` is off, the job logs that and writes nothing.

## Inputs

The job reads stored rows. It does not search the web.

- Company name: the saved overview name, otherwise the domain's company name, otherwise the domain.
- Company overview already saved for that domain. Source URLs are left out. An empty overview is sent as an empty object, and the model must not guess the business.
- Signal rows for that domain: signal type, human label, human meaning, catalog description, catalog product angle, need weight, scan count, and evidence. Scan ids are left out. Long evidence lists are cut at 8 items.

With no account argument, the job takes the top 20 tier A and B accounts by composite score. An account in that 20 that already has signal insights is skipped.

With `--account`, it runs that one domain even if it is outside the top 20, and it rewrites insights that are already saved.

## Output

JSON saved on `account_scores.signal_insights`:

- `company_name`
- `section_summary`: 6 to 8 sentences that use the overview so the representative can place the findings in that company's business. Per-signal cards stay short.
- one object per input signal, in the same order, with `signal_type`, `title`, `summary`, `evidence_summary`, `evidence_details`, `product_angle`, and `priority`

The model must include every supplied signal and must not add signals. It rewrites the catalog product angle into plain language. It must not copy the catalog string.

Priority on the saved row is not the model's word. The job overwrites it from `need_weight`: high at 70 or above, medium from 40 to 69, low from 1 to 39, informational at 0.

A bad answer is tried twice more. If it still fails, that account is skipped and any older insights stay.

The Signals tab shows these cards. Show proof still opens the stored scan evidence.

## Prompt

`pipeline/ai/prompts/signal-insights/v1.md`, loaded through the prompt registry as skill `signal-insights`. The model is the `agent` entry in `pipeline/ai/models.py`. The response shape is the `signal_insights` schema on the API call.

## Examples

One account, including a rewrite:

```bash
.venv/bin/python run.py signal-insights --account icealion.com
```

Top 20, skipping accounts that already have insights:

```bash
.venv/bin/python run.py signal-insights
```

`runner.py --all` uses the second form. The Generate signal insights button on an account page uses the first form, and it is shown only when insights are missing.
