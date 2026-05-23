"""
generate_risk.py
Filters active accounts under 6 months tenure, computes churn
risk signals, and writes at_risk_accounts.csv ranked by risk score.
"""

import pandas as pd
import numpy as np
from pathlib import Path

BASE = Path(__file__).parent
REF_DATE = pd.Timestamp('2024-12-31')
TENURE_WINDOW = 6  # months

# ── Load ──────────────────────────────────────────────────────────────────────
accounts = pd.read_csv(BASE / 'ravenstack_accounts.csv', parse_dates=['signup_date'])
subs     = pd.read_csv(BASE / 'ravenstack_subscriptions.csv')
usage_raw = pd.read_csv(BASE / 'ravenstack_feature_usage.csv', parse_dates=['usage_date'])
tickets   = pd.read_csv(BASE / 'ravenstack_support_tickets.csv')

# ── Clean ─────────────────────────────────────────────────────────────────────
usage = usage_raw.drop_duplicates(subset='usage_id')   # remove 21 ID collisions

# ── Filter: active accounts under 6 months tenure ─────────────────────────────
accounts['tenure_months'] = (
    (REF_DATE - accounts['signup_date']).dt.days / 30.44
).round(2)

cohort = accounts[
    (accounts['churn_flag'] == False) &
    (accounts['tenure_months'] < TENURE_WINDOW)
].copy()

print(f'Cohort: {len(cohort)} active accounts under {TENURE_WINDOW} months tenure')

# ── Build usage indexed by account ────────────────────────────────────────────
sub_to_acct = subs.set_index('subscription_id')['account_id']
usage = usage.copy()
usage['account_id'] = usage['subscription_id'].map(sub_to_acct)
usage = usage.dropna(subset=['account_id'])


def weekly_trend(events: pd.DataFrame) -> tuple[str, float]:
    """
    Fits a linear slope over weekly usage totals within the account's tenure window.
    Returns (label, score) where score=1 means strongly declining,
    score=0.5 means flat/unknown, score=0 means strongly growing.
    Threshold: slope/mean > ±5% per week is considered directional.
    """
    if len(events) < 4:
        return ('insufficient data', 0.5)

    weekly = (
        events.assign(week=events['usage_date'].dt.to_period('W'))
        .groupby('week', observed=True)['usage_count']
        .sum()
        .reset_index(name='total')
        .sort_values('week')
    )
    if len(weekly) < 3:
        return ('insufficient data', 0.5)

    x = np.arange(len(weekly), dtype=float)
    y = weekly['total'].values.astype(float)

    if y.std() == 0:
        return ('flat', 0.5)

    slope = np.polyfit(x, y, 1)[0]
    mean_y = y.mean() or 1.0
    rel_slope = slope / mean_y  # relative change per week as fraction of mean

    # ±5% per week is the materiality threshold
    label = 'declining' if rel_slope < -0.05 else ('growing' if rel_slope > 0.05 else 'flat')
    # Map relative slope to 0–1 risk score (declining = high risk)
    score = float(np.clip(0.5 - rel_slope * 4.0, 0.0, 1.0))
    return (label, round(score, 4))


# ── Per-account metrics ───────────────────────────────────────────────────────
records = []
for _, acct in cohort.iterrows():
    aid = acct['account_id']

    # Filter usage to events that fall within this account's tenure window
    signup_ts = acct['signup_date']
    acct_usage = usage[
        (usage['account_id'] == aid) &
        (usage['usage_date'] >= signup_ts)
    ]
    acct_tix = tickets[tickets['account_id'] == aid]

    # Usage signals
    n_events   = len(acct_usage)
    error_rate = float((acct_usage['error_count'] > 0).mean()) if n_events > 0 else 0.0
    trend_label, trend_score = weekly_trend(acct_usage)

    # Support signals
    n_escalated = int((acct_tix['escalation_flag'] == True).sum())

    # ── Composite risk score (weights sum to 1.0) ─────────────────────────────
    # trend_score already 0–1 (1 = declining)
    error_component     = error_rate                  # 0–1
    escalation_component = min(n_escalated / 2.0, 1.0)  # caps at 2 tickets

    risk_score = (
        0.40 * trend_score +
        0.35 * error_component +
        0.25 * escalation_component
    )

    records.append({
        'account_id':         aid,
        'account_name':       acct['account_name'],
        'plan_tier':          acct['plan_tier'],
        'industry':           acct['industry'],
        'country':            acct['country'],
        'signup_date':        acct['signup_date'].date(),
        'tenure_months':      round(acct['tenure_months'], 1),
        'weekly_trend':       trend_label,
        'trend_score':        round(trend_score, 4),
        'total_usage_events': n_events,
        'error_rate':         round(error_rate, 4),
        'escalated_tickets':  n_escalated,
        'risk_score':         round(risk_score, 4),
    })

out = pd.DataFrame(records).sort_values('risk_score', ascending=False).reset_index(drop=True)

out['risk_tier'] = pd.cut(
    out['risk_score'],
    bins=[0.0, 0.4, 0.6, 1.01],
    labels=['Low', 'Medium', 'High'],
    include_lowest=True,
)

out.to_csv(BASE / 'at_risk_accounts.csv', index=False)

print(f'\nWrote {len(out)} rows to at_risk_accounts.csv')
print('\nRisk tier breakdown:')
print(out['risk_tier'].value_counts().sort_index().to_string())
print('\nTop 15 accounts:')
print(out.head(15)[['account_name', 'plan_tier', 'tenure_months', 'weekly_trend',
                      'error_rate', 'escalated_tickets', 'risk_score', 'risk_tier']].to_string(index=False))

# ── MRR at risk ───────────────────────────────────────────────────────────────
# Sum active subscription MRR (end_date null, churn_flag false) per account
active_subs = subs[(subs['end_date'].isna()) & (subs['churn_flag'] == False)]
mrr_by_acct = active_subs.groupby('account_id')['mrr_amount'].sum()
out['mrr'] = out['account_id'].map(mrr_by_acct).fillna(0)

flagged = out[out['risk_tier'].isin(['High', 'Medium'])].copy()
total_mrr_at_risk = flagged['mrr'].sum()


def explain(row) -> str:
    reasons = []
    if row['weekly_trend'] == 'declining':
        reasons.append('weekly engagement is trending downward')
    elif row['weekly_trend'] == 'insufficient data':
        reasons.append('product engagement is too low to establish a usage pattern')
    er = row['error_rate']
    if er >= 0.40:
        reasons.append(f'{er:.0%} of feature interactions are failing with errors')
    elif er >= 0.28:
        reasons.append(f'elevated feature error rate ({er:.0%}) suggesting product friction')
    esc = int(row['escalated_tickets'])
    if esc >= 2:
        reasons.append(f'{esc} escalated support tickets indicating unresolved issues')
    elif esc == 1:
        reasons.append('1 escalated support ticket on record')
    if not reasons:
        if row['risk_score'] > 0.55:
            reasons.append('multiple moderate signals across engagement, errors, and support')
        else:
            reasons.append('early-tenure account showing below-average product engagement')
    return 'Flagged because ' + ' and '.join(reasons[:2]) + '.'


# ── agent_output.txt ──────────────────────────────────────────────────────────
tier_counts = out['risk_tier'].value_counts()
high_n   = int(tier_counts.get('High',   0))
medium_n = int(tier_counts.get('Medium', 0))
low_n    = int(tier_counts.get('Low',    0))

lines = [
    f'EARLY CHURN MONITOR — AGENT OUTPUT',
    f'Generated: {REF_DATE.date()}  |  Cohort: active accounts < {TENURE_WINDOW} months tenure',
    f'',
    f'SUMMARY',
    f'  Total accounts monitored : {len(out)}',
    f'  High risk  (score > 0.6) : {high_n}',
    f'  Medium risk (score 0.4-0.6) : {medium_n}',
    f'  Low risk   (score < 0.4) : {low_n}',
    f'  Total MRR at risk (high + medium) : ${total_mrr_at_risk:,.0f}/mo',
    f'',
    f'SCORING METHOD',
    f'  risk_score = 0.40 * weekly_trend + 0.35 * error_rate + 0.25 * escalation_score',
    f'  Weekly trend: linear regression over within-tenure usage events',
    f'                declining = rel. slope < -5%/week',
    f'  Error rate:   share of usage events with error_count > 0',
    f'  Escalations:  min(escalated_tickets / 2, 1)',
    f'',
]

if high_n > 0:
    lines.append('HIGH RISK ACCOUNTS')
    for _, row in out[out['risk_tier'] == 'High'].iterrows():
        lines += [
            f'',
            f'  {row["account_name"]}',
            f'    Plan           : {row["plan_tier"]}',
            f'    Tenure         : {row["tenure_months"]} months',
            f'    Weekly trend   : {row["weekly_trend"]}',
            f'    Error rate     : {row["error_rate"]:.0%}',
            f'    Escalations    : {int(row["escalated_tickets"])}',
            f'    Risk score     : {row["risk_score"]}',
            f'    MRR            : ${row["mrr"]:,.0f}/mo',
            f'    Signal         : {explain(row)}',
        ]
    lines.append('')

if medium_n > 0:
    lines.append('MEDIUM RISK ACCOUNTS')
    for _, row in out[out['risk_tier'] == 'Medium'].iterrows():
        lines += [
            f'',
            f'  {row["account_name"]}',
            f'    Plan           : {row["plan_tier"]}',
            f'    Tenure         : {row["tenure_months"]} months',
            f'    Weekly trend   : {row["weekly_trend"]}',
            f'    Error rate     : {row["error_rate"]:.0%}',
            f'    Escalations    : {int(row["escalated_tickets"])}',
            f'    Risk score     : {row["risk_score"]}',
            f'    MRR            : ${row["mrr"]:,.0f}/mo',
            f'    Signal         : {explain(row)}',
        ]
    lines.append('')

output_path = BASE / 'agent_output.txt'
output_path.write_text('\n'.join(lines), encoding='utf-8')
print(f'\nWrote agent_output.txt  ({len(flagged)} flagged accounts, ${total_mrr_at_risk:,.0f}/mo MRR at risk)')
