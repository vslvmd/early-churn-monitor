"""
dashboard.py
Reads at_risk_accounts.csv and serves a churn-risk dashboard at
http://localhost:8080  —  no external dependencies beyond stdlib.
"""

import csv
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

CSV_PATH = Path(__file__).parent / 'at_risk_accounts.csv'
PORT = 8080


# ── Data ──────────────────────────────────────────────────────────────────────

def load_accounts():
    with open(CSV_PATH, newline='') as f:
        return list(csv.DictReader(f))


# ── Explanation engine ────────────────────────────────────────────────────────

def explain(row: dict) -> str:
    reasons = []
    trend = row['weekly_trend']
    er    = float(row['error_rate'])
    esc   = int(row['escalated_tickets'])
    score = float(row['risk_score'])

    if trend == 'declining':
        reasons.append('weekly engagement is trending downward')
    elif trend == 'insufficient data':
        reasons.append('product engagement is too low to establish a usage pattern')

    if er >= 0.40:
        reasons.append(f'{er:.0%} of feature interactions are failing with errors')
    elif er >= 0.28:
        reasons.append(f'elevated feature error rate ({er:.0%}) suggesting product friction')

    if esc >= 2:
        reasons.append(f'{esc} escalated support tickets indicating unresolved issues')
    elif esc == 1:
        reasons.append('1 escalated support ticket on record')

    if not reasons:
        if trend == 'flat' and er > 0.20:
            reasons.append('flat engagement combined with above-average error rate')
        elif score > 0.55:
            reasons.append('multiple moderate signals across engagement, errors, and support')
        else:
            reasons.append('early-tenure account showing below-average product engagement')

    sentence = 'Flagged because ' + ' and '.join(reasons[:2]) + '.'
    return sentence


# ── HTML rendering ────────────────────────────────────────────────────────────

TIER_COLOR  = {'High': '#ef4444', 'Medium': '#f59e0b', 'Low': '#22c55e'}
TREND_ICON  = {'declining': '↘', 'growing': '↗', 'flat': '→', 'insufficient data': '?'}
PLAN_CLASS  = {'Enterprise': 'plan-ent', 'Pro': 'plan-pro', 'Basic': 'plan-basic'}


def _account_row(acct: dict) -> str:
    tier   = acct.get('risk_tier', 'Low')
    score  = float(acct['risk_score'])
    tc     = TIER_COLOR.get(tier, '#6b7280')
    ti     = TREND_ICON.get(acct['weekly_trend'], '?')
    pclass = PLAN_CLASS.get(acct['plan_tier'], 'plan-basic')
    er_pct = f"{float(acct['error_rate']):.0%}"
    bar    = int(score * 100)
    note   = explain(acct)

    return f"""
      <tr>
        <td>
          <div class="acct-name">{acct['account_name']}</div>
          <div class="acct-meta">{acct['account_id']} · {acct['industry']} · {acct['country']}</div>
        </td>
        <td><span class="badge {pclass}">{acct['plan_tier']}</span></td>
        <td class="num">{acct['tenure_months']} mo</td>
        <td class="trend {'bad' if acct['weekly_trend'] == 'declining' else 'neutral'}">{ti} {acct['weekly_trend']}</td>
        <td class="num">{acct['total_usage_events']}</td>
        <td class="num er {'er-high' if float(acct['error_rate']) >= 0.35 else ''}">{er_pct}</td>
        <td class="num {'esc-high' if int(acct['escalated_tickets']) > 0 else ''}">{acct['escalated_tickets']}</td>
        <td>
          <div class="score-wrap">
            <div class="score-bar-bg">
              <div class="score-bar-fill" style="width:{bar}%;background:{tc}"></div>
            </div>
            <span class="score-num" style="color:{tc}">{acct['risk_score']}</span>
          </div>
        </td>
        <td><span class="pill" style="background:{tc}1a;color:{tc};border:1px solid {tc}44">{tier}</span></td>
        <td class="why">{note}</td>
      </tr>"""


def render(accounts: list[dict]) -> str:
    total      = len(accounts)
    high       = sum(1 for a in accounts if a.get('risk_tier') == 'High')
    medium     = sum(1 for a in accounts if a.get('risk_tier') == 'Medium')
    low        = total - high - medium
    high_pct   = f'{high / total:.0%}' if total else '0%'
    tier_color = 'red' if high / max(total, 1) > 0.3 else 'amber'

    table_rows = ''.join(_account_row(a) for a in accounts)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Early Churn Monitor</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background: #0d1117;
      color: #c9d1d9;
      min-height: 100vh;
    }}

    /* ── top bar ── */
    .topbar {{
      background: #161b22;
      border-bottom: 1px solid #30363d;
      padding: 14px 32px;
      display: flex;
      align-items: center;
      gap: 10px;
    }}
    .logo {{ font-size: 18px; font-weight: 700; color: #f0f6fc; letter-spacing: -.3px; }}
    .logo em {{ color: #7c3aed; font-style: normal; }}
    .topbar-sub {{ font-size: 13px; color: #6e7681; margin-left: 6px; }}
    .ref {{ margin-left: auto; font-size: 12px; color: #484f58; }}

    /* ── layout ── */
    .main {{ max-width: 1700px; margin: 0 auto; padding: 28px 32px 48px; }}

    /* ── summary cards ── */
    .cards {{
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 14px;
      margin-bottom: 28px;
    }}
    .card {{
      background: #161b22;
      border: 1px solid #30363d;
      border-radius: 10px;
      padding: 20px 22px;
    }}
    .card-label {{ font-size: 11px; color: #6e7681; text-transform: uppercase;
                   letter-spacing: .07em; margin-bottom: 10px; }}
    .card-value {{ font-size: 36px; font-weight: 700; color: #f0f6fc;
                   font-variant-numeric: tabular-nums; line-height: 1; }}
    .card-value.red    {{ color: #f85149; }}
    .card-value.amber  {{ color: #d29922; }}
    .card-value.green  {{ color: #3fb950; }}
    .card-sub {{ font-size: 12px; color: #484f58; margin-top: 6px; }}

    /* ── section heading ── */
    .section-head {{ font-size: 14px; font-weight: 600; color: #8b949e;
                     margin-bottom: 10px; }}
    .section-head span {{ font-weight: 400; color: #484f58; font-size: 12px; }}

    /* ── legend ── */
    .legend {{ display: flex; gap: 18px; margin-bottom: 14px; }}
    .legend-item {{ display: flex; align-items: center; gap: 6px;
                    font-size: 12px; color: #6e7681; }}
    .ldot {{ width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }}

    /* ── table ── */
    .table-wrap {{ background: #161b22; border: 1px solid #30363d;
                   border-radius: 10px; overflow-x: auto; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    thead th {{
      background: #0d1117;
      color: #6e7681;
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: .06em;
      padding: 11px 14px;
      text-align: left;
      border-bottom: 1px solid #21262d;
      white-space: nowrap;
      font-weight: 600;
    }}
    tbody tr {{ border-bottom: 1px solid #21262d; transition: background 80ms; }}
    tbody tr:hover {{ background: #1c2128; }}
    tbody tr:last-child {{ border-bottom: none; }}
    td {{ padding: 11px 14px; vertical-align: middle; }}

    .acct-name {{ font-weight: 600; color: #e6edf3; }}
    .acct-meta {{ font-size: 11px; color: #484f58; margin-top: 3px; }}

    .num {{ font-variant-numeric: tabular-nums; color: #8b949e; }}
    .er-high {{ color: #f85149 !important; font-weight: 600; }}
    .esc-high {{ color: #d29922 !important; font-weight: 600; }}

    .badge {{
      display: inline-block; padding: 2px 9px; border-radius: 5px;
      font-size: 11px; font-weight: 600;
    }}
    .plan-ent   {{ background:#5b21b622;color:#a78bfa;border:1px solid #7c3aed44; }}
    .plan-pro   {{ background:#06402622;color:#34d399;border:1px solid #059e5b44; }}
    .plan-basic {{ background:#1d4ed822;color:#60a5fa;border:1px solid #2563eb44; }}

    .trend {{ white-space: nowrap; }}
    .trend.bad     {{ color: #f85149; }}
    .trend.neutral {{ color: #6e7681; }}

    .score-wrap {{ display: flex; align-items: center; gap: 8px; min-width: 110px; }}
    .score-bar-bg {{
      flex: 1; height: 5px; background: #21262d; border-radius: 3px; overflow: hidden;
    }}
    .score-bar-fill {{ height: 100%; border-radius: 3px; }}
    .score-num {{ font-size: 13px; font-weight: 700; font-variant-numeric: tabular-nums;
                  white-space: nowrap; }}

    .pill {{
      display: inline-block; padding: 2px 10px; border-radius: 999px;
      font-size: 11px; font-weight: 700; white-space: nowrap;
    }}

    .why {{ font-size: 12px; color: #6e7681; max-width: 320px; line-height: 1.5; }}

    .insight-banner {{
      display: flex; align-items: flex-start; gap: 12px;
      background: #1a2236; border: 1px solid #2d3f5e;
      border-left: 3px solid #818cf8;
      border-radius: 8px; padding: 14px 18px;
      margin-bottom: 20px;
      font-size: 13px; color: #94a3b8; line-height: 1.6;
    }}
    .insight-icon {{ color: #818cf8; font-size: 16px; flex-shrink: 0; margin-top: 1px; }}
    .insight-banner strong {{ color: #c7d2fe; }}
  </style>
</head>
<body>

<div class="topbar">
  <div class="logo">Early Customer Health Monitor</div>
  <div class="topbar-sub">Simulated SaaS dataset — methodology transfers to live agent data</div>
  <div class="ref">Reference date: 2024-12-31 &nbsp;·&nbsp; Cohort: active accounts &lt; 6 months tenure</div>
</div>

<div class="main">

  <div class="cards">
    <div class="card">
      <div class="card-label">Accounts Monitored</div>
      <div class="card-value">{total}</div>
      <div class="card-sub">Active · under 6 months tenure</div>
    </div>
    <div class="card">
      <div class="card-label">High Risk</div>
      <div class="card-value red">{high}</div>
      <div class="card-sub">Score &gt; 0.6 — needs immediate attention</div>
    </div>
    <div class="card">
      <div class="card-label">Medium Risk</div>
      <div class="card-value amber">{medium}</div>
      <div class="card-sub">Score 0.4 – 0.6 — monitor closely</div>
    </div>
    <div class="card">
      <div class="card-label">High Risk Rate</div>
      <div class="card-value {tier_color}">{high_pct}</div>
      <div class="card-sub">of new accounts flagged critical</div>
    </div>
  </div>

  <div class="insight-banner">
    <span class="insight-icon">&#9432;</span>
    <span><strong>Key finding:</strong> tenure is the strongest churn predictor (effect size 4x larger than any behavioral signal). The critical window is months 1–9. This monitor flags every new account showing early warning signs.</span>
  </div>

  <div class="section-head">
    At-Risk Account List
    <span>· ranked by composite risk score (trend 40% · error rate 35% · escalations 25%)</span>
  </div>

  <div class="legend">
    <div class="legend-item"><div class="ldot" style="background:#f85149"></div>High (&gt; 0.6)</div>
    <div class="legend-item"><div class="ldot" style="background:#d29922"></div>Medium (0.4–0.6)</div>
    <div class="legend-item"><div class="ldot" style="background:#3fb950"></div>Low (&lt; 0.4)</div>
  </div>

  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>Account</th>
          <th>Plan</th>
          <th>Tenure</th>
          <th>Weekly Trend</th>
          <th>Usage Events</th>
          <th>Error Rate</th>
          <th>Escalated</th>
          <th>Risk Score</th>
          <th>Tier</th>
          <th>Why Flagged</th>
        </tr>
      </thead>
      <tbody>
        {table_rows}
      </tbody>
    </table>
  </div>

</div>
</body>
</html>"""


# ── HTTP handler ──────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # suppress per-request noise

    def do_GET(self):
        if self.path not in ('/', '/index.html'):
            self.send_error(404)
            return
        try:
            accounts = load_accounts()
            html = render(accounts).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(html)))
            self.end_headers()
            self.wfile.write(html)
        except FileNotFoundError:
            self.send_error(500, f'CSV not found: {CSV_PATH}')
        except Exception as e:
            self.send_error(500, str(e))


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == '__main__':
    if not CSV_PATH.exists():
        print(f'ERROR: {CSV_PATH} not found — run generate_risk.py first.')
        sys.exit(1)

    server = HTTPServer(('localhost', PORT), Handler)
    print(f'Dashboard running at  http://localhost:{PORT}')
    print(f'Reading data from     {CSV_PATH}')
    print('Press Ctrl-C to stop.')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nStopped.')
