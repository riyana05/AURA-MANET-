"""
report.py — Side-by-side HTML report: the same moment analysed WITH and WITHOUT the LLM.

``python aura.py analyze --time 80`` writes ``results/report.html`` and opens it
in the browser.  The FastAPI server serves the same page at ``/ui``.
The page is self-contained (no external scripts or fonts).
"""

from __future__ import annotations

import html
from typing import Any

PAGE_CSS = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#1d2330;--muted:#5f6b7a;--line:#dde2e8;
--llm:#5b4bd6;--rule:#0f7b6c;--ok:#1a7f37;--warn:#b35900;--bad:#c62828;--chip:#eef1f5}
@media (prefers-color-scheme:dark){:root{--bg:#12151b;--card:#1b2029;--ink:#e6e9ef;--muted:#9aa5b4;
--line:#2c3340;--llm:#a89bff;--rule:#4fd1b5;--ok:#56d364;--warn:#f0a35e;--bad:#ff7b72;--chip:#252b36}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
main{max-width:1200px;margin:0 auto;padding:20px 16px 40px}
h1{font-size:20px;margin:0 0 4px}h2{font-size:15px;margin:0 0 10px}h3{font-size:13px;margin:14px 0 6px;
color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
.sub{color:var(--muted);margin-bottom:16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin-bottom:14px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}@media (max-width:820px){.grid{grid-template-columns:1fr}}
.col-llm{border-top:4px solid var(--llm)}.col-rule{border-top:4px solid var(--rule)}
.tag{display:inline-block;padding:1px 8px;border-radius:999px;background:var(--chip);font-size:12px;margin:0 4px 4px 0}
.sev-critical{color:var(--bad);font-weight:600}.sev-high{color:var(--warn);font-weight:600}
.banner{padding:10px 14px;border-radius:8px;margin-bottom:14px;font-weight:600}
.same{background:color-mix(in srgb,var(--ok) 15%,transparent);color:var(--ok)}
.diff{background:color-mix(in srgb,var(--warn) 18%,transparent);color:var(--warn)}
.action{font-size:18px;font-weight:700}.ok{color:var(--ok)}.bad{color:var(--bad)}.muted{color:var(--muted)}
ul{margin:4px 0 0;padding-left:18px}li{margin:2px 0}
table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid var(--line);padding:5px 6px;text-align:left}
th{color:var(--muted);font-weight:600}.num{text-align:right;font-variant-numeric:tabular-nums}
form{display:flex;gap:8px;flex-wrap:wrap;align-items:center}input,select,button{font:inherit;padding:5px 8px;
border-radius:6px;border:1px solid var(--line);background:var(--card);color:var(--ink)}
button{background:var(--llm);color:#fff;border:0;cursor:pointer}
"""


def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _fmt(v: float | None, digits: int = 3, suffix: str = "") -> str:
    return "–" if v is None else f"{v:.{digits}f}{suffix}"


def _metrics_table(ver: dict) -> str:
    before, after = ver.get("before"), ver.get("after")
    if not before or not after:
        return ""
    rows = [("PDR", "pdr", 3, ""), ("Throughput", "throughput_kbps", 1, " kbps"),
            ("Mean delay", "mean_delay_ms", 1, " ms"), ("Packets sent", "packets_sent", 0, "")]
    labels = ("without action", "with action") if ver.get("mode") == "counterfactual_rerun" \
        else (f"({before['start']:g}, {before['end']:g}] s", f"({after['start']:g}, {after['end']:g}] s")
    body = "".join(
        f"<tr><td>{n}</td><td class='num'>{_fmt(before.get(k), d, s)}</td>"
        f"<td class='num'>{_fmt(after.get(k), d, s)}</td></tr>"
        for n, k, d, s in rows
    )
    return (f"<table><tr><th></th><th class='num'>{labels[0]}</th><th class='num'>{labels[1]}</th></tr>"
            f"{body}</table>")


def _what_if(res: dict) -> str:
    chk = res.get("recommendation_check") or {}
    status = chk.get("status")
    if not status or status == "same_as_final":
        return ""
    rec = _e(chk.get("recommended"))
    if status != "tested":
        why = {"nothing_to_execute": "it changes nothing in the network",
               "not_applicable": "no concrete action could be built",
               "failed": _e(chk.get("error"))}.get(status, _e(status))
        return f"<h3>What if the recommendation ({rec}) were followed?</h3><div class='muted'>Not tested: {why}.</div>"
    ver = chk.get("verification") or {}
    ex = chk.get("execution") or {}
    st = ver.get("status", "–")
    cls = {"success": "ok", "degraded": "bad"}.get(st, "muted")
    params = ", ".join(f"{k}={v}" for k, v in (chk.get("action") or {}).get("parameters", {}).items())
    body = (f"<div class='{cls}'><b>{_e(st)}</b> <span class='muted'>({_e(ver.get('mode'))})</span></div>"
            + _metrics_table(ver) if ex.get("executed") else
            f"<div class='muted'>Not executed: {_e(ex.get('details') or ex.get('status'))}</div>")
    return (f"<h3>What if the recommendation ({rec}) were followed?</h3>"
            f"<div class='muted'>{_e(params)}</div>{body}")


def _fact_check(d: dict) -> str:
    fc = d.get("fact_check") or {}
    if not fc:
        return ""
    if fc.get("passed"):
        return "<h3>Fact-check</h3><div class='ok'>LLM connectivity claims match the measured topology.</div>"
    items = []
    if fc.get("wrongly_claimed_disconnected"):
        items.append(f"claimed flows {fc['wrongly_claimed_disconnected']} disconnected, but they have a path "
                     f"(really disconnected: {fc['measured_disconnected_flows']})")
    if fc.get("wrongly_claimed_isolated"):
        items.append(f"claimed nodes {fc['wrongly_claimed_isolated']} isolated "
                     f"(really isolated: {fc['measured_isolated_nodes']})")
    return "<h3>Fact-check</h3>" + "".join(f"<div class='bad'>✗ {_e(i)}</div>" for i in items)


def _column(res: dict, kind: str, title: str) -> str:
    d = res.get("diagnosis") or {}
    a = res.get("recovery_action") or {}
    v = res.get("validation") or {}
    ex = res.get("execution_result") or {}
    ver = res.get("verification_result") or {}

    method = d.get("method", "none")
    method_note = {"llm_rag": "LLM + RAG", "rule_based": "rule-based", "none": "not needed"}.get(method, method)
    findings = "".join(
        f"<li><b>{_e(f['fault'])}</b> <span class='muted'>conf {f['confidence']:.2f}"
        f"{' · nodes ' + _e(f['affected_nodes']) if f.get('affected_nodes') else ''}</span>"
        + ("<ul>" + "".join(f"<li class='muted'>{_e(x)}</li>" for x in f.get("evidence", [])[:5]) + "</ul>"
           if f.get("evidence") else "")
        + "</li>"
        for f in d.get("findings", [])
    ) or "<li class='muted'>none</li>"
    sources = "".join(f"<span class='tag'>{_e(s['chunk_id'])}</span>" for s in d.get("sources", []))
    notes = "".join(f"<li>{_e(n)}</li>" for n in d.get("notes", []))

    status = ver.get("status", "–")
    status_cls = {"success": "ok", "degraded": "bad"}.get(status, "muted")
    params = a.get("parameters") or {}
    param_txt = ", ".join(f"{k}={v}" for k, v in params.items() if k != "score")

    return f"""
<section class="card col-{kind}">
  <h2>{_e(title)} <span class="tag">{_e(method_note)}</span></h2>
  <h3>Root cause</h3><div>{_e(d.get('root_cause', '–'))}</div>
  <div class="muted">LLM/rules recommend: <b>{_e(d.get('recommended_action', '–'))}</b>
     (confidence {_fmt(d.get('confidence'), 2)})</div>
  <h3>Findings &amp; evidence</h3><ul>{findings}</ul>
  {f"<h3>Reasoning</h3><div>{_e(d['reasoning'])}</div>" if d.get('reasoning') else ''}
  {f"<h3>Knowledge cited</h3><div>{sources}</div>" if sources else ''}
  {_fact_check(d)}
  {f"<h3>Notes</h3><ul>{notes}</ul>" if notes else ''}
  <h3>Final decision</h3>
  <div class="action">{_e(a.get('action_type', '–'))}</div>
  <div class="muted">{_e(param_txt)}</div>
  <div>{_e(a.get('rationale', ''))}</div>
  <h3>Validation &amp; execution</h3>
  <div>valid: <b>{_e(v.get('valid'))}</b> · executable: <b>{_e(v.get('executable'))}</b>
     · execution: <b>{_e(ex.get('status'))}</b></div>
  {''.join(f"<div class='bad'>{_e(x)}</div>" for x in v.get('errors', []))}
  <h3>Verification</h3>
  <div class="{status_cls}"><b>{_e(status)}</b> <span class="muted">({_e(ver.get('mode'))})</span></div>
  {_metrics_table(ver)}
  {''.join(f"<div class='muted'>{_e(n)}</div>" for n in ver.get('notes', []))}
  {_what_if(res)}
</section>"""


def render_comparison(with_llm: dict, without_llm: dict, llm_name: str | None, form: str = "") -> str:
    """Build the full HTML page for one time step."""
    t = with_llm.get("current_time")
    w = with_llm.get("window_metrics") or {}
    anomalies = with_llm.get("anomalies") or []
    chips = "".join(
        f"<span class='tag sev-{_e(a['severity'])}'>{_e(a['type'])} · {_e(a['severity'])}</span>"
        for a in anomalies
    ) or "<span class='muted'>no anomalies</span>"
    act_llm = (with_llm.get("recovery_action") or {}).get("action_type")
    act_rule = (without_llm.get("recovery_action") or {}).get("action_type")
    banner = (f"<div class='banner same'>Both modes chose the same action: {_e(act_llm)}</div>"
              if act_llm == act_rule else
              f"<div class='banner diff'>Decisions differ: with LLM → {_e(act_llm)}, "
              f"without LLM → {_e(act_rule)}</div>")
    base = (with_llm.get("baseline_action") or {}).get("action", {})
    llm_title = f"With LLM ({llm_name})" if llm_name else "With LLM (not configured)"

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AURA-MANET Analysis</title><style>{PAGE_CSS}</style></head><body><main>
{form}
<h1>AURA-MANET analysis at t = {_e(t)} s</h1>
<div class="sub">Run {_e(with_llm.get('simulation_id'))} · protocol {_e(with_llm.get('protocol'))}
 · range {_e(with_llm.get('comm_range_m'))} m · last {_e(w.get('end', 0) - w.get('start', 0) if w else '')} s:
 PDR {_fmt(w.get('pdr'))}, {_e(w.get('packets_delivered'))}/{_e(w.get('packets_sent'))} packets delivered,
 throughput {_fmt(w.get('throughput_kbps'), 1, ' kbps')}</div>
<div class="card"><h2>Anomalies detected ({len(anomalies)})</h2>{chips}</div>
{banner}
<div class="grid">
{_column(with_llm, 'llm', llm_title)}
{_column(without_llm, 'rule', 'Without LLM (rule-based)')}
</div>
<div class="card"><h2>Rule-based baseline (for comparison)</h2>
<b>{_e(base.get('action_type'))}</b> <span class="muted">{_e(base.get('parameters'))} — {_e(base.get('rationale'))}</span>
</div>
</main></body></html>"""


def ui_form(time: float | None, backend: str, start: float, end: float) -> str:
    """Small form shown at the top of the /ui page."""
    sel = lambda b: " selected" if b == backend else ""  # noqa: E731
    return f"""<div class="card"><form method="get" action="/ui">
<label>Time (s) <input type="number" name="time" min="{start:g}" max="{end:g}" step="1"
 value="{'' if time is None else f'{time:g}'}" required></label>
<label>Backend <select name="backend"><option value="dry_run"{sel('dry_run')}>dry_run (record only)</option>
<option value="ns3_rerun"{sel('ns3_rerun')}>ns3_rerun (execute in ns-3)</option></select></label>
<button type="submit">Analyse</button><span class="muted">Runs the pipeline with and without the LLM.</span>
</form></div>"""


def render_ui_start(backend: str, start: float, end: float) -> str:
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'><title>AURA-MANET Analysis</title>"
            f"<style>{PAGE_CSS}</style></head><body><main>{ui_form(None, backend, start, end)}"
            f"<h1>AURA-MANET analysis</h1><div class='sub'>Pick a simulation time "
            f"({start:g}–{end:g} s) to compare the LLM and rule-based results.</div></main></body></html>")
