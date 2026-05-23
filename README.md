*Built to demonstrate the analyst layer above an AI agent system — translating automated data signals into decisions leadership can act on.*

# Early Churn Monitor

A SaaS customer health dashboard that identifies accounts at risk of churning
within their first six months, using usage trends, feature error rates, and
support escalation signals.

---

## Business question

**Which combination of signals best predicts which accounts are at risk of
churning — so a customer success team can intervene before it happens?**

Key finding from the analysis: tenure is the strongest churn predictor, with
an effect size 4× larger than any behavioral signal (Cohen's d = 0.85 vs ≤ 0.19
for all others). Churned accounts leave at a median of ~9 months; retained
accounts average ~15 months. The critical intervention window is months 1–9.

---

## What it does

1. Filters to active accounts with under 6 months tenure — the window where
   intervention is most likely to change the outcome.
2. Computes three early-warning signals per account:
   - **Weekly usage trend** — linear regression over within-tenure usage events;
     a relative slope below −5%/week is flagged as declining.
   - **Feature error rate** — fraction of usage events that logged at least one
     error, indicating product friction.
   - **Escalated support tickets** — count of tickets marked escalated,
     normalised and capped at 2.
3. Combines them into a composite risk score (trend 40% · error rate 35% ·
   escalations 25%) and assigns a tier: High (> 0.6), Medium (0.4–0.6),
   Low (< 0.4).
4. Serves a web dashboard at `http://localhost:8080` showing summary stats,
   a ranked at-risk table, and a plain-English explanation for every flagged
   account.

---

## Data source

Built on the **RavenStack synthetic SaaS dataset** by River @ Rivalytics,
available on Kaggle:

> kaggle.com/datasets/rivalytics/saas-subscription-and-churn-analytics-dataset

Five tables (500 accounts · 5 000 subscriptions · 25 000 usage events ·
2 000 support tickets · 600 churn events), fully synthetic, MIT-like license.
Credit the original author if you reuse or remix the data.

This dataset is used as a realistic proxy for live SaaS agent data. The
methodology — signal selection, scoring, and dashboard structure — transfers
directly to any live subscription dataset with equivalent fields.

---

## How to run

```bash
# 1. Generate the scored CSV (reads the five ravenstack_*.csv source files)
python generate_risk.py

# 2. Start the dashboard (reads at_risk_accounts.csv)
python dashboard.py
# then open http://localhost:8080
```

Dependencies: `pandas`, `numpy` (stdlib `http.server` for the dashboard — no
web framework needed).

```bash
pip install pandas numpy
```

---

## Files

| File | Description |
|---|---|
| `generate_risk.py` | Cleans data, computes signals, outputs `at_risk_accounts.csv` |
| `dashboard.py` | Reads the CSV and serves the web dashboard |
| `ravenstack_*.csv` | Source data (five tables) |
| `at_risk_accounts.csv` | Generated output — excluded from version control |

---

## Transferring to live data

The only changes needed to run this against a real subscription dataset are:

- Point `BASE` in `generate_risk.py` at your live CSV exports (or swap the
  `pd.read_csv` calls for database queries).
- Update `REF_DATE` to `pd.Timestamp('today')`.
- Adjust column names in the field mappings if your schema differs.

Signal logic, scoring weights, and the dashboard are dataset-agnostic.
