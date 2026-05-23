"""
dashboard.py
  /           → CEO Summary      (light theme)
  /details    → Analyst Dashboard (dark theme)
  /agent-output → agent_output.txt as plain text

No external dependencies beyond stdlib.
"""

import csv
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

CSV_PATH          = Path(__file__).parent / 'at_risk_accounts.csv'
AGENT_OUTPUT_PATH = Path(__file__).parent / 'agent_output.txt'
SUBS_PATH         = Path(__file__).parent / 'ravenstack_subscriptions.csv'
PORT = 8080


# ── Data ──────────────────────────────────────────────────────────────────────

def load_accounts():
    with open(CSV_PATH, newline='') as f:
        return list(csv.DictReader(f))

def load_mrr_by_account():
    """Sum active subscription MRR (no end_date, not churned) per account."""
    mrr = {}
    try:
        with open(SUBS_PATH, newline='') as f:
            for r in csv.DictReader(f):
                if not r['end_date'].strip() and r['churn_flag'] == 'False':
                    aid = r['account_id']
                    mrr[aid] = mrr.get(aid, 0.0) + float(r['mrr_amount'] or 0)
    except FileNotFoundError:
        pass
    return mrr


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


# ── Layer 1: CEO Summary (light theme) ───────────────────────────────────────

def render_ceo(accounts: list[dict], mrr_by_acct: dict) -> str:
    total    = len(accounts)
    high_accounts   = [a for a in accounts if a.get('risk_tier') == 'High']
    medium_accounts = [a for a in accounts if a.get('risk_tier') == 'Medium']
    high_n   = len(high_accounts)
    medium_n = len(medium_accounts)
    high_pct = f'{high_n / total:.0%}' if total else '0%'

    mrr_at_risk = sum(mrr_by_acct.get(a['account_id'], 0)
                      for a in high_accounts + medium_accounts)

    # Table: all high-risk + top 5 medium
    table_accounts = high_accounts + medium_accounts[:5]

    def ceo_row(acct):
        tier = acct.get('risk_tier', 'Low')
        mrr  = mrr_by_acct.get(acct['account_id'], 0)
        note = explain(acct)
        tier_style = {
            'High':   'color:#cf222e;background:#fff0ee;border:1px solid #ffc1bb',
            'Medium': 'color:#7d4e00;background:#fff8e1;border:1px solid #f5c842',
            'Low':    'color:#1a7f37;background:#e6f4ea;border:1px solid #a8d5b5',
        }.get(tier, '')
        plan_style = {
            'Enterprise': 'color:#5a32a3;background:#f6f0ff;border:1px solid #c4b5fd',
            'Pro':        'color:#0f766e;background:#f0fdfa;border:1px solid #5eead4',
            'Basic':      'color:#1e40af;background:#eff6ff;border:1px solid #93c5fd',
        }.get(acct['plan_tier'], '')
        return f"""
      <tr>
        <td>
          <div class="c-name">{acct['account_name']}</div>
          <div class="c-meta">{acct['industry']} &middot; {acct['country']}</div>
        </td>
        <td><span class="c-badge" style="{plan_style}">{acct['plan_tier']}</span></td>
        <td class="c-num">{acct['tenure_months']} mo</td>
        <td><span class="c-tier" style="{tier_style}">{tier}</span></td>
        <td class="c-num c-mrr">${mrr:,.0f}<span class="c-mo">/mo</span></td>
        <td class="c-why">{note}</td>
      </tr>"""

    table_rows = ''.join(ceo_row(a) for a in table_accounts)
    shown_medium = min(5, medium_n)
    more_medium  = medium_n - shown_medium

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
      padding: 14px 32px; display: flex; align-items: center; gap: 12px;
    }}
    .logo {{ font-size: 17px; font-weight: 700; color: #1f2328; letter-spacing: -.2px; }}
    .topbar-sub {{ font-size: 13px; color: #656d76; }}
    .topbar-right {{ margin-left: auto; display: flex; align-items: center; gap: 10px; }}
    .view-analyst {{
      padding: 6px 14px; background: #f6f8fa; border: 1px solid #d0d7de;
      border-radius: 6px; color: #1f2328; font-size: 13px; font-weight: 500;
      text-decoration: none; transition: background 120ms;
    }}
    .view-analyst:hover {{ background: #eaeef2; }}
    .main {{ max-width: 1100px; margin: 0 auto; padding: 32px 32px 56px; }}
    .cards {{
      display: grid; grid-template-columns: repeat(4, 1fr);
      gap: 16px; margin-bottom: 28px;
    }}
    .card {{
      background: #fff; border: 1px solid #d0d7de; border-radius: 10px;
      padding: 22px 24px; box-shadow: 0 1px 3px rgba(0,0,0,.04);
    }}
    .card-label {{ font-size: 11px; color: #656d76; text-transform: uppercase;
                   letter-spacing: .07em; margin-bottom: 10px; font-weight: 600; }}
    .card-value {{ font-size: 36px; font-weight: 700; color: #1f2328;
                   font-variant-numeric: tabular-nums; line-height: 1; }}
    .card-value.red   {{ color: #cf222e; }}
    .card-value.amber {{ color: #7d4e00; }}
    .card-sub {{ font-size: 12px; color: #656d76; margin-top: 6px; }}
    .insight-banner {{
      display: flex; align-items: flex-start; gap: 12px;
      background: #fff; border: 1px solid #d0d7de;
      border-left: 3px solid #6e40c9;
      border-radius: 8px; padding: 14px 18px; margin-bottom: 24px;
      font-size: 13px; color: #1f2328; line-height: 1.6;
      box-shadow: 0 1px 3px rgba(0,0,0,.04);
    }}
    .insight-icon {{ color: #6e40c9; font-size: 16px; flex-shrink: 0; margin-top: 1px; }}
    .insight-banner strong {{ color: #6e40c9; }}
    .section-head {{
      font-size: 14px; font-weight: 600; color: #1f2328;
      margin-bottom: 4px;
    }}
    .section-sub {{
      font-size: 12px; color: #656d76; margin-bottom: 14px;
    }}
    .table-wrap {{
      background: #fff; border: 1px solid #d0d7de; border-radius: 10px;
      overflow-x: auto; box-shadow: 0 1px 3px rgba(0,0,0,.04);
      margin-bottom: 20px;
    }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    thead th {{
      background: #f6f8fa; color: #656d76; font-size: 11px;
      text-transform: uppercase; letter-spacing: .06em; font-weight: 600;
      padding: 11px 16px; text-align: left;
      border-bottom: 1px solid #d0d7de; white-space: nowrap;
    }}
    tbody tr {{ border-bottom: 1px solid #eaeef2; transition: background 80ms; }}
    tbody tr:hover {{ background: #f6f8fa; }}
    tbody tr:last-child {{ border-bottom: none; }}
    td {{ padding: 12px 16px; vertical-align: middle; }}
    .c-name {{ font-weight: 600; color: #1f2328; }}
    .c-meta {{ font-size: 11px; color: #656d76; margin-top: 2px; }}
    .c-num {{ font-variant-numeric: tabular-nums; color: #656d76; }}
    .c-mrr {{ font-weight: 600; color: #1f2328; }}
    .c-mo  {{ font-size: 11px; color: #656d76; font-weight: 400; }}
    .c-badge {{
      display: inline-block; padding: 2px 9px; border-radius: 5px;
      font-size: 11px; font-weight: 600;
    }}
    .c-tier {{
      display: inline-block; padding: 2px 10px; border-radius: 999px;
      font-size: 11px; font-weight: 700;
    }}
    .c-why {{ font-size: 12px; color: #656d76; max-width: 340px; line-height: 1.5; }}
    .more-row td {{
      text-align: center; color: #656d76; font-size: 12px;
      padding: 10px; background: #f6f8fa; border-radius: 0 0 10px 10px;
    }}
    .footer-actions {{
      display: flex; align-items: center; gap: 12px; margin-top: 8px;
    }}
    .view-full-btn {{
      display: inline-block; padding: 9px 20px;
      background: #1f2328; border: 1px solid #1f2328; border-radius: 6px;
      color: #fff; font-size: 13px; font-weight: 600; text-decoration: none;
      transition: background 120ms;
    }}
    .view-full-btn:hover {{ background: #32383f; border-color: #32383f; }}
    .footer-note {{ font-size: 12px; color: #656d76; }}
{_MODAL_CSS}
  </style>
</head>
<body>

<div class="topbar">
  <div class="logo">Early Churn Monitor</div>
  <div class="topbar-sub">CEO Summary &nbsp;&middot;&nbsp; Active accounts &lt; 6 months</div>
  <div class="topbar-right">
    <button class="export-btn" onclick="openBriefModal()">&#128196; Export for Brief</button>
    <a class="view-analyst" href="/details">View Full Analysis &rarr;</a>
  </div>
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
      <div class="card-value red">{high_n}</div>
      <div class="card-sub">Need immediate CS outreach</div>
    </div>
    <div class="card">
      <div class="card-label">MRR at Risk</div>
      <div class="card-value amber">${mrr_at_risk:,.0f}</div>
      <div class="card-sub">High + medium risk accounts</div>
    </div>
    <div class="card">
      <div class="card-label">High Risk Rate</div>
      <div class="card-value {'red' if high_n / max(total,1) > 0.1 else 'amber'}">{high_pct}</div>
      <div class="card-sub">of new accounts flagged critical</div>
    </div>
  </div>

  <div class="insight-banner">
    <span class="insight-icon">&#9432;</span>
    <span><strong>Key finding:</strong> tenure is the strongest churn predictor (effect size 4&times; larger than any behavioral signal). The critical window is months&nbsp;1&ndash;9. This monitor flags every new account showing early warning signs.</span>
  </div>

  <div class="section-head">Accounts Requiring Attention</div>
  <div class="section-sub">All {high_n} high-risk + top 5 medium-risk accounts &middot; ranked by composite risk score</div>

  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>Account</th>
          <th>Plan</th>
          <th>Tenure</th>
          <th>Risk</th>
          <th>MRR</th>
          <th>Why Flagged</th>
        </tr>
      </thead>
      <tbody>
        {table_rows}
        {'<tr class="more-row"><td colspan="6">+ ' + str(more_medium) + ' more medium-risk accounts &mdash; <a href="/details">view all in analyst dashboard</a></td></tr>' if more_medium > 0 else ''}
      </tbody>
    </table>
  </div>

  <div class="footer-actions">
    <a class="view-full-btn" href="/details">View Full Analysis &rarr;</a>
    <span class="footer-note">All {total} monitored accounts &middot; scoring: engagement trend 40% &middot; error rate 35% &middot; escalations 25%</span>
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
                accounts    = load_accounts()
                mrr_by_acct = load_mrr_by_account()
                _serve_html(self, render_ceo(accounts, mrr_by_acct))

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
