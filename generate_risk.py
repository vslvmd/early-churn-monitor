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
