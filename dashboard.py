"""
dashboard.py
  /           → CEO Summary      (light theme)
  /details    → Analyst Dashboard (dark theme)
  /agent-output → agent_output.txt as plain text

No external dependencies beyond stdlib.
"""

import csv
import sys
from collections import defaultdict
from datetime import date
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

CSV_PATH            = Path(__file__).parent / 'at_risk_accounts.csv'
AGENT_OUTPUT_PATH   = Path(__file__).parent / 'agent_output.txt'
SUBS_PATH           = Path(__file__).parent / 'ravenstack_subscriptions.csv'
ACCOUNTS_PATH       = Path(__file__).parent / 'ravenstack_accounts.csv'
CHURN_EVENTS_PATH   = Path(__file__).parent / 'ravenstack_churn_events.csv'
REF_DATE            = date(2024, 12, 31)
PORT = 8080


# ── Data ──────────────────────────────────────────────────────────────────────

def load_accounts():
    with open(CSV_PATH, newline='') as f:
        return list(csv.DictReader(f))

BRIEF = {
    "status": "alert",
    "metrics": [
        {"label": "MRR at Risk",          "value": "$622,674", "delta": "33 of 126 accounts flagged",       "direction": "down"},
        {"label": "High Risk",            "value": "2",        "delta": "$13,326/mo combined MRR",          "direction": "down"},
        {"label": "Accounts Monitored",   "value": "126",      "delta": "Active · under 6 months tenure", "direction": "neutral"},
        {"label": "High Risk Rate",       "value": "2%",       "delta": "of new accounts flagged critical", "direction": "down"},
    ],
    "signal": "A systemic product error problem is driving churn risk across early-tenure accounts. 33 of 126 new accounts are flagged representing $622,674/mo MRR at risk. The critical window is months 1–9 — without proactive CS outreach this cohort will produce compounding cancellations within 4–8 weeks.",
    "flags": [
        {"severity": "red",   "text": "Company_100 and Company_139 are high risk — declining usage, 40–50% error rates, escalated tickets. Combined $13,326/mo MRR. Needs CS outreach this week."},
        {"severity": "amber", "text": "31 medium risk accounts with declining engagement in the danger window. Top 5 represent $100k+ MRR. Monitor closely."},
        {"severity": "green", "text": "93 low risk accounts are stable with growing or flat usage trends."},
    ],
    "questions": [
        "Six accounts show 100% error rates under 1.5 months tenure — is this a product bug or onboarding gap, and who owns the investigation?",
        "Company_139 is a Basic plan at 3.8 months with $12,422/mo MRR — is that MRR figure correct, and has any CS touchpoint happened?",
        "With average tenure at churn of 9 months, what is the current intervention playbook for accounts flagged in months 1–5?",
    ],
}


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

    return 'Flagged because ' + ' and '.join(reasons[:2]) + '.'


# ── Cohort retention ─────────────────────────────────────────────────────────

def compute_cohort_retention() -> list[dict]:
    """
    Returns one row per signup-month cohort:
      { label: 'Jan 2024', n: 22, ret: {1: 0.91, 3: 0.77, 6: None, 9: None} }
    ret[m] is the fraction still active at month m, or None if not yet reached.
    Accounts with churn_flag=True but no churn_events record are excluded
    (churn date unknown — would distort survival estimates).
    """
    MILESTONES = [1, 3, 6, 9]

    accounts = []
    with open(ACCOUNTS_PATH, newline='') as f:
        accounts = list(csv.DictReader(f))

    # Earliest churn date per account from churn_events
    earliest_churn: dict[str, date] = {}
    with open(CHURN_EVENTS_PATH, newline='') as f:
        for r in csv.DictReader(f):
            d = date.fromisoformat(r['churn_date'])
            aid = r['account_id']
            if aid not in earliest_churn or d < earliest_churn[aid]:
                earliest_churn[aid] = d

    cohorts: dict[tuple, list] = defaultdict(list)
    for a in accounts:
        signup = date.fromisoformat(a['signup_date'])
        churned = a['churn_flag'] == 'True'
        if churned:
            churn_dt = earliest_churn.get(a['account_id'])
            if churn_dt is None:
                continue  # unknown churn date — exclude
        else:
            churn_dt = None
        cohorts[(signup.year, signup.month)].append(
            {'signup': signup, 'churn_date': churn_dt}
        )

    rows = []
    for (year, month) in sorted(cohorts.keys()):
        members = cohorts[(year, month)]
        n = len(members)
        if n < 3:
            continue
        cohort_start    = date(year, month, 1)
        cohort_age_days = (REF_DATE - cohort_start).days
        label = cohort_start.strftime('%b %Y')

        ret = {}
        for m in MILESTONES:
            milestone_days = 30.44 * m
            if cohort_age_days < milestone_days:
                ret[m] = None  # cohort hasn't reached this milestone yet
                continue
            survived = sum(
                1 for mb in members
                if mb['churn_date'] is None
                or (mb['churn_date'] - mb['signup']).days > milestone_days
            )
            ret[m] = round(survived / n, 4)

        if ret.get(1) is not None:   # only show cohorts old enough for month-1
            rows.append({'label': label, 'n': n, 'ret': ret})

    return rows


# ── Shared: modal HTML + JS (dark, embedded in both pages) ───────────────────
# Plain strings — braces here are literal, no f-string escaping needed.

_MODAL_CSS = """
    .export-btn {
      padding: 7px 14px;
      background: #238636; border: 1px solid #2ea043; border-radius: 6px;
      color: #f0f6fc; font-size: 13px; font-weight: 600; cursor: pointer;
      white-space: nowrap; flex-shrink: 0; transition: background 120ms;
    }
    .export-btn:hover { background: #2ea043; }
    .modal-backdrop {
      display: none; position: fixed; inset: 0;
      background: rgba(0,0,0,.65); z-index: 100;
      align-items: center; justify-content: center;
    }
    .modal-backdrop.open { display: flex; }
    .modal {
      background: #161b22; border: 1px solid #30363d; border-radius: 12px;
      width: min(780px, 92vw); max-height: 80vh;
      display: flex; flex-direction: column;
      box-shadow: 0 24px 48px rgba(0,0,0,.6);
    }
    .modal-header {
      display: flex; align-items: center; gap: 12px;
      padding: 16px 20px; border-bottom: 1px solid #21262d; flex-shrink: 0;
    }
    .modal-title { font-size: 15px; font-weight: 600; color: #f0f6fc; }
    .modal-note {
      font-size: 12px; color: #58a6ff;
      background: #1a2a3a; border: 1px solid #2d4a6a;
      border-radius: 5px; padding: 4px 10px; margin-left: auto;
    }
    .modal-close {
      background: none; border: none; color: #6e7681; font-size: 20px;
      cursor: pointer; line-height: 1; padding: 2px 6px; border-radius: 4px;
    }
    .modal-close:hover { color: #e6edf3; background: #21262d; }
    .modal-body { overflow-y: auto; padding: 16px 20px; flex: 1; }
    .modal-body pre {
      font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace;
      font-size: 12px; line-height: 1.7; color: #c9d1d9;
      white-space: pre-wrap; word-break: break-word; margin: 0;
    }
    .modal-footer {
      padding: 12px 20px; border-top: 1px solid #21262d;
      display: flex; align-items: center; gap: 10px; flex-shrink: 0;
    }
    .copy-btn {
      padding: 7px 16px; background: #21262d; border: 1px solid #30363d;
      border-radius: 6px; color: #c9d1d9; font-size: 13px; font-weight: 600;
      cursor: pointer; transition: background 120ms;
    }
    .copy-btn:hover { background: #30363d; }
    .copy-btn.copied { background: #1f4c2e; border-color: #2ea043; color: #3fb950; }
    .modal-footer-note { font-size: 12px; color: #484f58; }"""

_MODAL_HTML = """
<div class="modal-backdrop" id="briefBackdrop" onclick="handleBackdropClick(event)">
  <div class="modal">
    <div class="modal-header">
      <div class="modal-title">Agent Output</div>
      <div class="modal-note">&#128161; Paste this into Claude to generate the CEO brief</div>
      <button class="modal-close" onclick="closeModal()" title="Close">&times;</button>
    </div>
    <div class="modal-body">
      <pre id="agentOutputText">Loading…</pre>
    </div>
    <div class="modal-footer">
      <button class="copy-btn" id="copyBtn" onclick="copyToClipboard()">Copy to clipboard</button>
      <span class="modal-footer-note">agent_output.txt &mdash; generated by generate_risk.py</span>
    </div>
  </div>
</div>

<script>
  function openBriefModal() {
    document.getElementById('briefBackdrop').classList.add('open');
    document.getElementById('agentOutputText').textContent = 'Loading…';
    fetch('/agent-output')
      .then(r => r.ok ? r.text() : Promise.reject('HTTP ' + r.status))
      .then(text => { document.getElementById('agentOutputText').textContent = text; })
      .catch(err => { document.getElementById('agentOutputText').textContent = 'Could not load agent_output.txt — run generate_risk.py first.\\n\\nDetail: ' + err; });
  }
  function closeModal() {
    document.getElementById('briefBackdrop').classList.remove('open');
    const btn = document.getElementById('copyBtn');
    btn.textContent = 'Copy to clipboard';
    btn.classList.remove('copied');
  }
  function handleBackdropClick(e) {
    if (e.target === document.getElementById('briefBackdrop')) closeModal();
  }
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });
  function copyToClipboard() {
    const text = document.getElementById('agentOutputText').textContent;
    navigator.clipboard.writeText(text).then(() => {
      const btn = document.getElementById('copyBtn');
      btn.textContent = 'Copied!';
      btn.classList.add('copied');
      setTimeout(() => { btn.textContent = 'Copy to clipboard'; btn.classList.remove('copied'); }, 2000);
    });
  }
</script>"""


# ── Layer 1: CEO Brief (light theme, JSON-driven) ─────────────────────────────

def render_brief(brief: dict, cohort_rows: list) -> str:
    status = brief['status']
    status_styles = {
        'alert':   ('background:#fff0ee;color:#cf222e;border:1px solid #ffc1bb', '● ALERT'),
        'warning': ('background:#fff8e1;color:#7d4e00;border:1px solid #f5c842', '● WARNING'),
        'ok':      ('background:#e6f4ea;color:#1a7f37;border:1px solid #a8d5b5', '● OK'),
    }
    badge_style, badge_label = status_styles.get(status, status_styles['alert'])

    dir_html = {
        'down':    '<span style="color:#cf222e;font-size:11px;">&#9660; </span>',
        'up':      '<span style="color:#1a7f37;font-size:11px;">&#9650; </span>',
        'neutral': '',
    }
    def metric_card(m):
        arrow = dir_html.get(m['direction'], '')
        return f"""
    <div class="card">
      <div class="card-label">{m['label']}</div>
      <div class="card-value">{m['value']}</div>
      <div class="card-sub">{arrow}{m['delta']}</div>
    </div>"""

    cards_html = ''.join(metric_card(m) for m in brief['metrics'])

    flag_styles = {
        'red':   ('border-left:3px solid #cf222e;background:#fff8f7', '#cf222e', '&#9632;'),
        'amber': ('border-left:3px solid #d4a017;background:#fffcf0', '#7d4e00', '&#9632;'),
        'green': ('border-left:3px solid #2da44e;background:#f3fbf6', '#1a7f37', '&#9632;'),
    }
    def flag_item(f):
        style, color, icon = flag_styles.get(f['severity'], flag_styles['amber'])
        return f"""
    <div class="flag" style="{style}">
      <span class="flag-icon" style="color:{color}">{icon}</span>
      <span class="flag-text">{f['text']}</span>
    </div>"""

    flags_html = ''.join(flag_item(f) for f in brief['flags'])

    questions_html = ''.join(
        f'<li>{q}</li>' for q in brief['questions']
    )

    def ret_cell(v):
        if v is None:
            return '<td><span class="ret-na">&mdash;</span></td>'
        pct = f'{v:.0%}'
        cls = 'ret-green' if v >= 0.70 else ('ret-amber' if v >= 0.50 else 'ret-red')
        return f'<td><span class="{cls}">{pct}</span></td>'

    cohort_table_rows = ''.join(
        f'<tr><td>{r["label"]}</td><td class="c-n">{r["n"]}</td>'
        + ''.join(ret_cell(r['ret'].get(m)) for m in [1, 3, 6, 9])
        + '</tr>'
        for r in cohort_rows
    )

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
      background: #f6f8fa; color: #1f2328; min-height: 100vh;
    }}

    .topbar {{
      background: #fff; border-bottom: 1px solid #d0d7de;
      padding: 14px 32px; display: flex; align-items: center; gap: 14px;
    }}
    .logo {{ font-size: 17px; font-weight: 700; color: #1f2328; letter-spacing: -.2px; flex-shrink: 0; }}
    .status-badge {{
      display: inline-block; padding: 3px 10px; border-radius: 999px;
      font-size: 11px; font-weight: 700; letter-spacing: .04em;
    }}
    .topbar-right {{ margin-left: auto; }}
    .view-analyst {{
      padding: 7px 16px; background: #f6f8fa; border: 1px solid #d0d7de;
      border-radius: 6px; color: #1f2328; font-size: 13px; font-weight: 600;
      text-decoration: none; transition: background 120ms; white-space: nowrap;
    }}
    .view-analyst:hover {{ background: #eaeef2; }}

    .main {{ max-width: 960px; margin: 0 auto; padding: 36px 32px 64px; }}

    .cards {{
      display: grid; grid-template-columns: repeat(4, 1fr);
      gap: 16px; margin-bottom: 32px;
    }}
    .card {{
      background: #fff; border: 1px solid #d0d7de; border-radius: 10px;
      padding: 22px 22px 18px; box-shadow: 0 1px 2px rgba(0,0,0,.04);
    }}
    .card-label {{
      font-size: 11px; color: #656d76; text-transform: uppercase;
      letter-spacing: .07em; margin-bottom: 10px; font-weight: 600;
    }}
    .card-value {{
      font-size: 34px; font-weight: 700; color: #1f2328;
      font-variant-numeric: tabular-nums; line-height: 1;
    }}
    .card-sub {{ font-size: 12px; color: #656d76; margin-top: 8px; line-height: 1.4; }}

    .signal-block {{
      background: #fff; border: 1px solid #d0d7de;
      border-left: 3px solid #1f2328;
      border-radius: 8px; padding: 20px 24px;
      margin-bottom: 20px; box-shadow: 0 1px 2px rgba(0,0,0,.04);
    }}
    .signal-label {{
      font-size: 10px; font-weight: 700; text-transform: uppercase;
      letter-spacing: .1em; color: #656d76; margin-bottom: 10px;
    }}
    .signal-text {{
      font-size: 15px; line-height: 1.7; color: #1f2328; font-weight: 400;
    }}

    .flags {{ display: flex; flex-direction: column; gap: 10px; margin-bottom: 28px; }}
    .flag {{
      display: flex; align-items: flex-start; gap: 12px;
      padding: 14px 18px; border-radius: 8px;
      border: 1px solid #e5e7eb;
    }}
    .flag-icon {{ font-size: 10px; flex-shrink: 0; margin-top: 3px; }}
    .flag-text {{ font-size: 13px; color: #1f2328; line-height: 1.6; }}

    .questions-block {{
      background: #fff; border: 1px solid #d0d7de; border-radius: 10px;
      padding: 22px 24px; box-shadow: 0 1px 2px rgba(0,0,0,.04);
      margin-bottom: 28px;
    }}
    .questions-label {{
      font-size: 12px; font-weight: 700; text-transform: uppercase;
      letter-spacing: .07em; color: #656d76; margin-bottom: 16px;
    }}
    .questions-block ol {{
      padding-left: 20px; display: flex; flex-direction: column; gap: 14px;
    }}
    .questions-block li {{
      font-size: 14px; color: #1f2328; line-height: 1.6; padding-left: 4px;
    }}

    .footer-bar {{
      display: flex; align-items: center; gap: 14px;
    }}
    .view-full-btn {{
      display: inline-block; padding: 9px 20px;
      background: #1f2328; border: 1px solid #1f2328; border-radius: 6px;
      color: #fff; font-size: 13px; font-weight: 600; text-decoration: none;
      transition: background 120ms;
    }}
    .view-full-btn:hover {{ background: #32383f; }}
    .footer-note {{ font-size: 12px; color: #656d76; }}

    .signals-found {{ margin-bottom: 28px; }}
    .signals-heading {{
      font-size: 13px; font-weight: 600; color: #1f2328; margin-bottom: 12px;
    }}
    .signals-wrap {{
      background: #fff; border: 1px solid #d0d7de; border-radius: 10px;
      overflow-x: auto; box-shadow: 0 1px 2px rgba(0,0,0,.04);
    }}
    .signals-wrap table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    .signals-wrap thead th {{
      background: #f6f8fa; color: #656d76; font-size: 11px; font-weight: 600;
      text-transform: uppercase; letter-spacing: .06em;
      padding: 10px 16px; text-align: right; border-bottom: 1px solid #d0d7de;
    }}
    .signals-wrap thead th:first-child {{ text-align: left; }}
    .signals-wrap tbody tr {{ border-bottom: 1px solid #eaeef2; }}
    .signals-wrap tbody tr:last-child {{ border-bottom: none; }}
    .signals-wrap tbody tr:hover {{ background: #f6f8fa; }}
    .signals-wrap tr.sig-highlight {{
      background: #fffbeb; border-left: 3px solid #d4a017;
    }}
    .signals-wrap tr.sig-highlight:hover {{ background: #fff8d6; }}
    .signals-wrap tr.sig-highlight td {{ font-weight: 700; color: #1f2328; }}
    .signals-wrap td {{
      padding: 10px 16px; text-align: right; font-size: 13px;
      font-variant-numeric: tabular-nums; color: #1f2328;
    }}
    .signals-wrap td:first-child {{ text-align: left; }}
    .signals-caption {{
      font-size: 12px; color: #656d76; margin-top: 10px; font-style: italic;
    }}

    .cohort-section {{ margin-bottom: 28px; }}
    .cohort-heading {{
      font-size: 13px; font-weight: 600; color: #1f2328;
      margin-bottom: 12px;
    }}
    .cohort-wrap {{
      background: #fff; border: 1px solid #d0d7de; border-radius: 10px;
      overflow-x: auto; box-shadow: 0 1px 2px rgba(0,0,0,.04);
    }}
    .cohort-wrap table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    .cohort-wrap thead th {{
      background: #f6f8fa; color: #656d76; font-size: 11px; font-weight: 600;
      text-transform: uppercase; letter-spacing: .06em;
      padding: 10px 16px; text-align: center; border-bottom: 1px solid #d0d7de;
    }}
    .cohort-wrap thead th:first-child {{ text-align: left; }}
    .cohort-wrap tbody tr {{ border-bottom: 1px solid #eaeef2; }}
    .cohort-wrap tbody tr:last-child {{ border-bottom: none; }}
    .cohort-wrap tbody tr:hover {{ background: #f6f8fa; }}
    .cohort-wrap td {{
      padding: 9px 16px; text-align: center;
      font-size: 13px; font-variant-numeric: tabular-nums;
    }}
    .cohort-wrap td:first-child {{ text-align: left; font-weight: 600; color: #1f2328; }}
    .cohort-wrap td.c-n {{ color: #656d76; font-size: 12px; }}
    .ret-green {{ color: #1a7f37; background: #e6f4ea; border-radius: 4px;
                  font-weight: 600; display: inline-block; padding: 2px 8px; }}
    .ret-amber {{ color: #7d4e00; background: #fff8e1; border-radius: 4px;
                  font-weight: 600; display: inline-block; padding: 2px 8px; }}
    .ret-red   {{ color: #cf222e; background: #fff0ee; border-radius: 4px;
                  font-weight: 600; display: inline-block; padding: 2px 8px; }}
    .ret-na    {{ color: #bbb; font-size: 12px; }}
  </style>
</head>
<body>

<div class="topbar">
  <div class="logo">Early Churn Monitor</div>
  <span class="status-badge" style="{badge_style}">{badge_label}</span>
  <div class="topbar-right">
    <a class="view-analyst" href="/details">View Full Analysis &rarr;</a>
  </div>
</div>

<div class="main">

  <div class="cards">{cards_html}
  </div>

  <div class="signal-block">
    <div class="signal-label">Situation</div>
    <div class="signal-text">{brief['signal']}</div>
  </div>

  <div class="flags">{flags_html}
  </div>

  <div class="signals-found">
    <div class="signals-heading">What the data found</div>
    <div class="signals-wrap">
      <table>
        <thead>
          <tr>
            <th>Signal</th>
            <th>Churned avg</th>
            <th>Retained avg</th>
          </tr>
        </thead>
        <tbody>
          <tr class="sig-highlight">
            <td>Tenure</td>
            <td>9.0 months</td>
            <td>15.3 months</td>
          </tr>
          <tr>
            <td>Error rate</td>
            <td>29.8%</td>
            <td>31.0%</td>
          </tr>
          <tr>
            <td>Support tickets</td>
            <td>3.93</td>
            <td>4.02</td>
          </tr>
          <tr>
            <td>Escalation rate</td>
            <td>6.1%</td>
            <td>4.3%</td>
          </tr>
        </tbody>
      </table>
    </div>
    <div class="signals-caption">Tenure is the only signal with meaningful separation between churned and retained accounts.</div>
  </div>

  <div class="cohort-section">
    <div class="cohort-heading">Retention by signup cohort — where customers drop off</div>
    <div class="cohort-wrap">
      <table>
        <thead>
          <tr>
            <th>Cohort</th>
            <th>N</th>
            <th>Month 1</th>
            <th>Month 3</th>
            <th>Month 6</th>
            <th>Month 9</th>
          </tr>
        </thead>
        <tbody>
          {cohort_table_rows}
        </tbody>
      </table>
    </div>
  </div>

  <div class="questions-block">
    <div class="questions-label">Questions for Leadership</div>
    <ol>{questions_html}
    </ol>
  </div>

  <div class="footer-bar">
    <a class="view-full-btn" href="/details">View Full Analysis &rarr;</a>
    <span class="footer-note">Signals: engagement trend 40% &middot; error rate 35% &middot; escalations 25% &middot; reference date 2024-12-31</span>
  </div>

</div>
</body>
</html>"""


# ── Layer 2: Analyst Dashboard (dark theme) ───────────────────────────────────

TIER_COLOR = {'High': '#ef4444', 'Medium': '#f59e0b', 'Low': '#22c55e'}
TREND_ICON = {'declining': '↘', 'growing': '↗', 'flat': '→', 'insufficient data': '?'}
PLAN_CLASS = {'Enterprise': 'plan-ent', 'Pro': 'plan-pro', 'Basic': 'plan-basic'}


def _analyst_row(acct: dict) -> str:
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
          <div class="acct-meta">{acct['account_id']} &middot; {acct['industry']} &middot; {acct['country']}</div>
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


def render_analyst(accounts: list[dict]) -> str:
    total      = len(accounts)
    high       = sum(1 for a in accounts if a.get('risk_tier') == 'High')
    medium     = sum(1 for a in accounts if a.get('risk_tier') == 'Medium')
    high_pct   = f'{high / total:.0%}' if total else '0%'
    tier_color = 'red' if high / max(total, 1) > 0.3 else 'amber'
    table_rows = ''.join(_analyst_row(a) for a in accounts)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Early Churn Monitor — Analyst View</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background: #0d1117; color: #c9d1d9; min-height: 100vh;
    }}
    .topbar {{
      background: #161b22; border-bottom: 1px solid #30363d;
      padding: 14px 32px; display: flex; align-items: center; gap: 12px;
    }}
    .back-link {{
      font-size: 13px; color: #8b949e; text-decoration: none;
      padding: 5px 10px; border: 1px solid #30363d; border-radius: 6px;
      transition: color 120ms, border-color 120ms; white-space: nowrap;
      flex-shrink: 0;
    }}
    .back-link:hover {{ color: #c9d1d9; border-color: #6e7681; }}
    .logo {{ font-size: 17px; font-weight: 700; color: #f0f6fc; letter-spacing: -.2px; }}
    .topbar-sub {{ font-size: 13px; color: #6e7681; }}
    .ref {{ margin-left: auto; font-size: 12px; color: #484f58; }}
    .main {{ max-width: 1700px; margin: 0 auto; padding: 28px 32px 48px; }}
    .cards {{
      display: grid; grid-template-columns: repeat(4, 1fr);
      gap: 14px; margin-bottom: 28px;
    }}
    .card {{
      background: #161b22; border: 1px solid #30363d; border-radius: 10px;
      padding: 20px 22px;
    }}
    .card-label {{ font-size: 11px; color: #6e7681; text-transform: uppercase;
                   letter-spacing: .07em; margin-bottom: 10px; }}
    .card-value {{ font-size: 36px; font-weight: 700; color: #f0f6fc;
                   font-variant-numeric: tabular-nums; line-height: 1; }}
    .card-value.red   {{ color: #f85149; }}
    .card-value.amber {{ color: #d29922; }}
    .card-value.green {{ color: #3fb950; }}
    .card-sub {{ font-size: 12px; color: #484f58; margin-top: 6px; }}
    .section-head {{ font-size: 14px; font-weight: 600; color: #8b949e; margin-bottom: 10px; }}
    .section-head span {{ font-weight: 400; color: #484f58; font-size: 12px; }}
    .legend {{ display: flex; gap: 18px; margin-bottom: 14px; }}
    .legend-item {{ display: flex; align-items: center; gap: 6px;
                    font-size: 12px; color: #6e7681; }}
    .ldot {{ width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }}
    .table-wrap {{ background: #161b22; border: 1px solid #30363d;
                   border-radius: 10px; overflow-x: auto; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    thead th {{
      background: #0d1117; color: #6e7681; font-size: 11px;
      text-transform: uppercase; letter-spacing: .06em;
      padding: 11px 14px; text-align: left;
      border-bottom: 1px solid #21262d; white-space: nowrap; font-weight: 600;
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
      border-radius: 8px; padding: 14px 18px; margin-bottom: 20px;
      font-size: 13px; color: #94a3b8; line-height: 1.6;
    }}
    .insight-icon {{ color: #818cf8; font-size: 16px; flex-shrink: 0; margin-top: 1px; }}
    .insight-banner strong {{ color: #c7d2fe; }}
{_MODAL_CSS}
  </style>
</head>
<body>

<div class="topbar">
  <a class="back-link" href="/">&larr; Summary</a>
  <div class="logo">Early Churn Monitor</div>
  <div class="topbar-sub">Analyst View</div>
  <div class="ref">Reference date: 2024-12-31 &nbsp;&middot;&nbsp; Cohort: active accounts &lt; 6 months tenure</div>
  <button class="export-btn" onclick="openBriefModal()">&#128196; Export for Brief</button>
</div>

{_MODAL_HTML}

<div class="main">

  <div class="cards">
    <div class="card">
      <div class="card-label">Accounts Monitored</div>
      <div class="card-value">{total}</div>
      <div class="card-sub">Active &middot; under 6 months tenure</div>
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
    <span><strong>Key finding:</strong> tenure is the strongest churn predictor (effect size 4&times; larger than any behavioral signal). The critical window is months&nbsp;1&ndash;9. This monitor flags every new account showing early warning signs.</span>
  </div>

  <div class="section-head">
    At-Risk Account List
    <span>&middot; ranked by composite risk score (trend 40% &middot; error rate 35% &middot; escalations 25%)</span>
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

def _serve_html(handler, html: str):
    body = html.encode('utf-8')
    handler.send_response(200)
    handler.send_header('Content-Type', 'text/html; charset=utf-8')
    handler.send_header('Content-Length', str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        try:
            if self.path in ('/', '/index.html'):
                cohort_rows = compute_cohort_retention()
                _serve_html(self, render_brief(BRIEF, cohort_rows))

            elif self.path == '/details':
                accounts = load_accounts()
                _serve_html(self, render_analyst(accounts))

            elif self.path == '/agent-output':
                body = AGENT_OUTPUT_PATH.read_bytes()
                self.send_response(200)
                self.send_header('Content-Type', 'text/plain; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            else:
                self.send_error(404)

        except FileNotFoundError as e:
            self.send_error(500, str(e))
        except Exception as e:
            self.send_error(500, str(e))


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == '__main__':
    if not CSV_PATH.exists():
        print(f'ERROR: {CSV_PATH} not found — run generate_risk.py first.')
        sys.exit(1)

    server = HTTPServer(('localhost', PORT), Handler)
    print(f'CEO Summary   →  http://localhost:{PORT}/')
    print(f'Analyst View  →  http://localhost:{PORT}/details')
    print(f'Agent Output  →  http://localhost:{PORT}/agent-output')
    print('Press Ctrl-C to stop.')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nStopped.')
