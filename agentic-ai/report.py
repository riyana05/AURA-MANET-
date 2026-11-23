"""
report.py — Sharp one-to-one comparison report for one moment.

Sections (top to bottom):
(the LLM chooses relay nodes; code validates and scores its paths)

1. Verdict line: do LLM and rules agree, and did the decision help.
2. Decision table: LLM column vs rule-based column, one question per row,
   differing rows highlighted.
3. Flow by flow: for every struggling flow, the AI system's route (relay nodes
   chosen for stable links) next to the plain shortest path a simple rule
   system would use, with hops and how long the weakest link is predicted to
   last; the better one is marked.
4. Node by node: which nodes the LLM blames vs the rules, and which nodes each
   route relies on.
5. Collapsed details (reasoning, evidence, knowledge, notes).

Routes come from the deterministic Navigator, never from the LLM.
``python aura.py analyze --time 80`` writes ``results/report.html`` and opens it;
the API serves the same page at ``/ui``.  Self-contained HTML, no scripts.
"""

from __future__ import annotations

import html
from typing import Any

import networkx as nx

ACTION = {
    "no_action": "No change",
    "wait_for_reconnection": "Wait for reconnection",
    "reroute_flow": "Reroute a flow",
    "increase_tx_range": "Stronger radio (range)",
    "switch_protocol": "Switch routing protocol",
    "isolate_node": "Isolate a node",
}

PAGE_CSS = """
:root{color-scheme:light;--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;
--line:#e1e0d9;--border:rgba(11,11,11,.10);--llm:#2a78d6;--rule:#eb6834;--good:#006300;--bad:#d03b3b;
--diff:#fff4d6;--diffbar:#fab219;--track:#e1e0d9;--agreebar:#0ca30c}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])){color-scheme:dark;--page:#0d0d0d;
--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--line:#2c2c2a;--border:rgba(255,255,255,.10);--llm:#3987e5;
--rule:#d95926;--good:#0ca30c;--diff:#33290f;--track:#2c2c2a}}
:root[data-theme="dark"]{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;
--line:#2c2c2a;--border:rgba(255,255,255,.10);--llm:#3987e5;--rule:#d95926;--good:#0ca30c;--diff:#33290f;
--track:#2c2c2a}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1020px;margin:0 auto;padding:24px 16px 48px}
h1{font-size:20px;margin:0 0 2px}h2{font-size:16px;margin:26px 0 4px}
.sub{color:var(--ink2);font-size:13px}.muted{color:var(--muted)}.hint{color:var(--muted);font-size:12px;margin:0 0 8px}
.verdict{margin:16px 0;padding:14px 16px;border-radius:10px;border:1px solid var(--border);
background:var(--surface);font-size:16px;font-weight:600;display:flex;gap:10px;align-items:center}
.verdict .icon{font-size:20px}.agree{border-left:6px solid var(--agreebar)}.differ{border-left:6px solid var(--diffbar)}
.card{background:var(--surface);border:1px solid var(--border);border-radius:12px;overflow-x:auto}
table{border-collapse:collapse;width:100%}
th,td{padding:9px 12px;text-align:left;vertical-align:top;border-bottom:1px solid var(--line)}
tr:last-child td{border-bottom:0}th{font-size:12px;font-weight:650;color:var(--ink2);white-space:nowrap}
td.q{width:24%;color:var(--ink2);font-size:13px}
.head-llm{border-top:4px solid var(--llm)}.head-rule{border-top:4px solid var(--rule)}
.tag{font-weight:500;font-size:12px;color:var(--muted);margin-left:4px}
tr.diff td{background:var(--diff)}td.q.mark::after{content:"  ≠";font-weight:700;color:var(--ink)}
.big{font-weight:650}.ok{color:var(--good);font-weight:650}.bad{color:var(--bad);font-weight:650}
.path{font-variant-numeric:tabular-nums;white-space:nowrap}
.node{display:inline-block;min-width:24px;padding:0 5px;border-radius:6px;border:1px solid var(--border);
text-align:center;margin:1px}.node.shared{opacity:.55}.node.only{border-color:var(--ink);font-weight:700}
.win{font-weight:700;color:var(--good)}.num{font-variant-numeric:tabular-nums;text-align:right}
.pdr{display:grid;grid-template-columns:58px minmax(60px,1fr) 44px;gap:4px 8px;align-items:center;font-size:12px}
.track{height:8px;background:var(--track);border-radius:4px;overflow:hidden}.fill{height:100%;border-radius:0 4px 4px 0}
.chip{display:inline-block;border:1px solid var(--border);border-radius:999px;padding:0 8px;margin:2px 4px 2px 0;font-size:13px}
.yes{font-weight:650}.dash{color:var(--muted)}
details{margin-top:12px;background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:10px 14px}
summary{cursor:pointer;font-weight:600}details ul{margin:6px 0;padding-left:18px}details h4{margin:10px 0 2px;font-size:13px;color:var(--ink2)}
form{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:16px}
input,select,button{font:inherit;padding:5px 8px;border-radius:6px;border:1px solid var(--border);background:var(--surface);color:var(--ink)}
button{background:var(--llm);color:#fff;border:0;cursor:pointer}
"""


def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _pct(v: float | None) -> str:
    return "–" if v is None else f"{100 * v:.0f}%"


def _secs(v: float | None) -> str:
    return "–" if v is None else f"{v:.1f} s"


# --------------------------------------------------------------------------- #
# Facts per mode                                                               #
# --------------------------------------------------------------------------- #


def _facts(res: dict) -> dict:
    d = res.get("diagnosis") or {}
    a = res.get("recovery_action") or {}
    ex = res.get("execution_result") or {}
    ver = res.get("verification_result") or {}
    fc = d.get("fact_check") or {}
    call = d.get("llm_call") or {}

    if ex.get("executed"):
        result = {"success": ("✓ Helped", "ok"), "degraded": ("✗ Made it worse", "bad")}.get(
            ver.get("status"), ("≈ Little change", ""))
    elif a.get("action_type") in ("no_action", "wait_for_reconnection"):
        result = ("No change made", "muted")
    elif a.get("action_type") in ("reroute_flow", "isolate_node"):
        result = ("Advice only: ns-3 can't force a route", "muted")
    else:
        result = ("Not tested (dry run)", "muted")

    p = a.get("parameters") or {}
    detail = {"increase_tx_range": lambda: f"{p.get('from_m', 0):g} m → {p.get('range_m', 0):g} m",
              "switch_protocol": lambda: f"{p.get('from', '?')} → {p.get('protocol', '?')}",
              "reroute_flow": lambda: f"flow {a.get('flow_id')} via " + " → ".join(map(str, p.get("path", [])))
              }.get(a.get("action_type"), lambda: "")()

    blamed = sorted({n for f in d.get("findings", []) for n in f.get("affected_nodes", [])})
    findings = d.get("findings") or []
    fault = (findings[0].get("fault") or "").lower().replace(" ", "_") if findings else ""
    return {
        "method": d.get("method"),
        "cause": d.get("root_cause") or "–",
        "confidence": d.get("confidence"),
        "advice": d.get("recommended_action"),
        "decision": a.get("action_type"),
        "detail": detail,
        "executed": bool(ex.get("executed")),
        "before": (ver.get("before") or {}).get("pdr") if ex.get("executed") else None,
        "after": (ver.get("after") or {}).get("pdr") if ex.get("executed") else None,
        "result": result,
        "fact": ("✓ Matches the measured network", "ok") if fc.get("passed") else
                (("✗ Got some facts wrong", "bad") if fc else ("–", "muted")),
        "proof": (f"{call.get('model')} · {call.get('latency_s')} s · "
                  f"{call.get('prompt_tokens')}→{call.get('completion_tokens')} tokens") if call else
                 ("Not used (rules only)" if d.get("method") != "llm_rag" else "–"),
        "blamed": blamed,
        "fault": fault,
    }


# --------------------------------------------------------------------------- #
# Flow-by-flow route comparison                                                #
# --------------------------------------------------------------------------- #


def _graph(topo: dict) -> nx.Graph:
    g = nx.Graph()
    g.add_nodes_from(n["id"] for n in topo.get("nodes", []))
    for link in topo.get("links", []):
        g.add_edge(link["a"], link["b"], lifetime=link.get("lifetime_s"))
    return g


def _weakest(g: nx.Graph, path: list[int]) -> float | None:
    vals = [g.edges[a, b].get("lifetime") for a, b in zip(path, path[1:])]
    return None if not vals or any(v is None for v in vals) else min(vals)


def _route(c: dict | None) -> dict | None:
    if not c:
        return None
    return {"path": c["path"], "hops": c["hop_count"], "life": c.get("min_link_lifetime_s"),
            "score": c.get("score"), "rejected": c.get("rejected"),
            "reasons": c.get("rejection_reasons") or [], "why": c.get("reason", "")}


def flow_comparison(res: dict) -> list[dict]:
    """One row per struggling flow: LLM-chosen route vs computed route vs shortest path."""
    g = _graph(res.get("topology") or {})
    rows = []
    for r in res.get("candidate_routes") or []:
        src, dst = r["source"], r["destination"]
        row = {"flow": r["flow_id"], "src": src, "dst": dst, "pdr": r.get("window_pdr"),
               "connected": r["connected"], "llm": _route(r.get("llm_route")),
               "llm_errors": r.get("llm_route_errors") or [], "computed": _route(r.get("best")),
               "short": None, "verdict": "", "winner": ""}
        if not r["connected"]:
            row["verdict"] = "No path at all (partition): no route can help"
            rows.append(row)
            continue
        sp = nx.shortest_path(g, src, dst)
        row["short"] = {"path": sp, "hops": len(sp) - 1, "life": _weakest(g, sp)}
        llm, comp = row["llm"], row["computed"]
        if llm is None and not row["llm_errors"]:
            row["verdict"] = "LLM did not choose (rules mode or no answer)"
        elif llm is None:
            row["verdict"], row["winner"] = "LLM path invalid", "computed"
        elif llm["rejected"]:
            row["verdict"], row["winner"] = "LLM path unsafe", "computed"
        elif comp is None:
            row["verdict"], row["winner"] = "Only the LLM found a safe path", "llm"
        elif llm["path"] == comp["path"]:
            row["verdict"], row["winner"] = "Same route", "same"
        elif (llm["score"] or 0) > (comp["score"] or 0) + 1e-9:
            row["verdict"], row["winner"] = f"LLM route better (score {llm['score']:.2f} vs {comp['score']:.2f})", "llm"
        elif (llm["score"] or 0) < (comp["score"] or 0) - 1e-9:
            row["verdict"], row["winner"] = f"Computed route better (score {comp['score']:.2f} vs {llm['score']:.2f})", "computed"
        else:
            row["verdict"], row["winner"] = "Equally good", "same"
        rows.append(row)
    return rows


def _path_html(path: list[int], others: list[list[int]]) -> str:
    seen = set().union(*[set(o) for o in others if o]) if others else set()
    cells = []
    for i, n in enumerate(path):
        relay = 0 < i < len(path) - 1
        cls = "node" + ((" only" if n not in seen else " shared") if relay else " shared")
        cells.append(f"<span class='{cls}'>{n}</span>")
    return "<span class='path'>" + " → ".join(cells) + "</span>"


def _route_cell(rt: dict | None, others: list[list[int]], errors: list[str] | None = None) -> str:
    if errors:
        return "<span class='bad'>✗ invalid</span><div class='muted'>" + _e("; ".join(errors)) + "</div>"
    if rt is None:
        return "<span class='dash'>–</span>"
    meta = f"{rt['hops']} hops · weakest link {_secs(rt['life'])}"
    if rt.get("score") is not None:
        meta += f" · score {rt['score']:.2f}"
    out = f"{_path_html(rt['path'], others)}<div class='muted'>{meta}</div>"
    if rt.get("rejected"):
        out += "<div class='bad'>✗ unsafe: " + _e("; ".join(rt["reasons"])) + "</div>"
    if rt.get("why"):
        out += f"<div class='muted'><i>“{_e(rt['why'])}”</i></div>"
    return out


def _flow_table(res: dict) -> str:
    rows = flow_comparison(res)
    if not rows:
        return "<div class='muted'>No struggling flows at this moment.</div>"
    body = ""
    for r in rows:
        head = (f"<td class='num'>{r['flow']}</td><td class='path'>{r['src']} → {r['dst']}</td>"
                f"<td class='num'>{_pct(r['pdr'])}</td>")
        if not r["connected"]:
            body += (f"<tr>{head}<td colspan='3' class='dash'>no route exists</td>"
                     f"<td class='muted'>{_e(r['verdict'])}</td></tr>")
            continue
        llm, comp, sh = r["llm"], r["computed"], r["short"]
        paths = {"llm": llm["path"] if llm else None, "comp": comp["path"] if comp else None, "sh": sh["path"]}
        differ = r["winner"] in ("llm", "computed")
        cls = {"llm": "win", "computed": "win"}.get(r["winner"], "muted")
        body += (f"<tr{' class=diff' if differ else ''}>{head}"
                 f"<td>{_route_cell(llm, [paths['comp'], paths['sh']], r['llm_errors'])}</td>"
                 f"<td>{_route_cell(comp, [paths['llm'], paths['sh']])}</td>"
                 f"<td>{_route_cell(sh, [paths['llm'], paths['comp']])}</td>"
                 f"<td class='{cls}'>{_e(r['verdict'])}</td></tr>")
    return ("<div class='card'><table><thead><tr><th>Flow</th><th>From → to</th><th>Delivered</th>"
            "<th class='head-llm'>LLM-chosen route</th><th class='head-rule'>Computed route (no LLM)</th>"
            f"<th>Plain shortest path</th><th>Better</th></tr></thead><tbody>{body}</tbody></table></div>")


# --------------------------------------------------------------------------- #
# Node-by-node comparison                                                      #
# --------------------------------------------------------------------------- #


def _node_table(with_llm: dict, without_llm: dict, L: dict, R: dict) -> str:
    topo = with_llm.get("topology") or {}
    group = {}
    for i, comp in enumerate(topo.get("components", [])):
        for n in comp:
            group[n] = "cut off" if len(comp) == 1 else f"group {i + 1} ({len(comp)} nodes)"
    flows = flow_comparison(with_llm)
    relays = {"llm": {}, "computed": {}}
    endpoints: dict[int, list[int]] = {}
    for r in flows:
        endpoints.setdefault(r["src"], []).append(r["flow"])
        endpoints.setdefault(r["dst"], []).append(r["flow"])
        for key in relays:
            rt = r.get(key)
            if rt and not rt.get("rejected"):
                for n in rt["path"][1:-1]:
                    relays[key].setdefault(n, []).append(r["flow"])
    nodes = sorted(set(L["blamed"]) | set(R["blamed"]) | set(relays["llm"]) | set(relays["computed"]))
    if not nodes:
        return "<div class='muted'>No nodes involved.</div>"

    def flag(v):
        return "<span class='yes'>✓</span>" if v else "<span class='dash'>–</span>"

    def flows_txt(d, n):
        return f"<span class='yes'>flow {', '.join(map(str, d[n]))}</span>" if n in d else "<span class='dash'>–</span>"

    body = ""
    for n in nodes:
        in_l, in_r = n in L["blamed"], n in R["blamed"]
        differ = in_l != in_r or (n in relays["llm"]) != (n in relays["computed"])
        role = f"endpoint of flow {', '.join(map(str, endpoints[n]))}" if n in endpoints else ""
        body += (f"<tr{' class=diff' if differ else ''}><td class='num'><span class='node'>{n}</span></td>"
                 f"<td class='muted'>{_e(group.get(n, '–'))}{'<br>' + _e(role) if role else ''}</td>"
                 f"<td>{flag(in_l)}</td><td>{flag(in_r)}</td>"
                 f"<td>{flows_txt(relays['llm'], n)}</td><td>{flows_txt(relays['computed'], n)}</td></tr>")
    return ("<div class='card'><table><thead><tr><th>Node</th><th>Where it is</th>"
            "<th class='head-llm'>LLM blames it</th><th class='head-rule'>Rules blame it</th>"
            "<th class='head-llm'>LLM uses it as relay</th><th class='head-rule'>Computed route uses it</th>"
            f"</tr></thead><tbody>{body}</tbody></table></div>")


# --------------------------------------------------------------------------- #
# Decision table + page                                                        #
# --------------------------------------------------------------------------- #


def _pdr_cell(before, after, color_var: str) -> str:
    if before is None or after is None:
        return "<span class='dash'>not tested</span>"
    rows = ""
    for label, v, col in (("Without", before, "var(--muted)"), ("With fix", after, color_var)):
        rows += (f"<span class='muted'>{label}</span><div class='track'><div class='fill' "
                 f"style='width:{100 * v:.1f}%;background:{col}'></div></div><span class='num'>{_pct(v)}</span>")
    return f"<div class='pdr'>{rows}</div>"


def _row(q: str, left: str, right: str, differ: bool) -> str:
    return (f"<tr{' class=diff' if differ else ''}><td class='q{' mark' if differ else ''}'>{_e(q)}</td>"
            f"<td>{left}</td><td>{right}</td></tr>")


def _details(res: dict, title: str) -> str:
    d = res.get("diagnosis") or {}
    parts = [f"<summary>{_e(title)}: reasoning, evidence and notes</summary>"]
    if d.get("reasoning"):
        parts.append(f"<h4>Reasoning</h4><div>{_e(d['reasoning'])}</div>")
    for f in d.get("findings", []):
        ev = "".join(f"<li>{_e(x)}</li>" for x in f.get("evidence", [])[:6])
        parts.append(f"<h4>{_e(f['fault'])} (confidence {f['confidence']:.2f})</h4><ul>{ev}</ul>")
    if d.get("sources"):
        parts.append("<h4>Knowledge consulted</h4><ul>" + "".join(
            f"<li><b>{_e(s['chunk_id'])}</b>: {_e(s['text'][:200])}…</li>" for s in d["sources"]) + "</ul>")
    if (res.get("recovery_action") or {}).get("rationale"):
        parts.append(f"<h4>Why this decision</h4><div>{_e(res['recovery_action']['rationale'])}</div>")
    if d.get("notes"):
        parts.append("<h4>Notes</h4><ul>" + "".join(f"<li>{_e(n)}</li>" for n in d["notes"]) + "</ul>")
    return "<details>" + "".join(parts) + "</details>"


def render_comparison(with_llm: dict, without_llm: dict, llm_name: str | None, form: str = "") -> str:
    """Build the report page for one time step."""
    L, R = _facts(with_llm), _facts(without_llm)
    t = with_llm.get("current_time")
    w = with_llm.get("window_metrics") or {}
    left_title = "With LLM" + (f" ({llm_name})" if llm_name else " (not configured)")

    name = lambda a: ACTION.get(a, a or "–")  # noqa: E731
    if L["decision"] == R["decision"]:
        tested = f" — {_pct(L['before'])} → {_pct(L['after'])} delivered" if L["executed"] else ""
        verdict = (f"<div class='verdict agree'><span class='icon'>✓</span><span>LLM and rules agree: "
                   f"{_e(name(L['decision']))}{' (' + _e(L['detail']) + ')' if L['detail'] else ''}. "
                   f"{_e(L['result'][0])}{tested}</span></div>")
    else:
        verdict = (f"<div class='verdict differ'><span class='icon'>≠</span><span>They disagree: LLM → "
                   f"{_e(name(L['decision']))}, rules → {_e(name(R['decision']))}</span></div>")

    conf = lambda v: "–" if v is None else f"{v:.2f}"  # noqa: E731
    cell = lambda txt, cls: f"<span class='{cls}'>{_e(txt)}</span>"  # noqa: E731
    act = lambda f: (f"<span class='big'>{_e(name(f['decision']))}</span>"  # noqa: E731
                     + (f"<div class='muted'>{_e(f['detail'])}</div>" if f["detail"] else ""))
    nodes_txt = lambda f: ", ".join(map(str, f["blamed"])) or "–"  # noqa: E731
    rows = "".join([
        _row("What went wrong", _e(L["cause"]) + (f"<div class='muted'>fault: {_e(L['fault'])}</div>" if L["fault"] else ""),
             _e(R["cause"]) + (f"<div class='muted'>fault: {_e(R['fault'])}</div>" if R["fault"] else ""),
             L["fault"] != R["fault"]),
        _row("Nodes blamed", _e(nodes_txt(L)), _e(nodes_txt(R)), L["blamed"] != R["blamed"]),
        _row("Confidence", conf(L["confidence"]), conf(R["confidence"]),
             abs((L["confidence"] or 0) - (R["confidence"] or 0)) >= 0.1),
        _row("Diagnosis advised", _e(name(L["advice"])), _e(name(R["advice"])), L["advice"] != R["advice"]),
        _row("Final decision", act(L), act(R), L["decision"] != R["decision"]),
        _row("Delivered (tested in ns-3)", _pdr_cell(L["before"], L["after"], "var(--llm)"),
             _pdr_cell(R["before"], R["after"], "var(--rule)"),
             (L["before"], L["after"]) != (R["before"], R["after"])),
        _row("Result", cell(*L["result"]), cell(*R["result"]), L["result"][0] != R["result"][0]),
        _row("LLM fact-check", cell(*L["fact"]), "<span class='dash'>– (no LLM)</span>", False),
        _row("LLM call (proof)", _e(L["proof"]), _e(R["proof"]), False),
    ])

    counts: dict[str, int] = {}
    for a in with_llm.get("anomalies") or []:
        counts[a["type"]] = counts.get(a["type"], 0) + 1
    problems = "".join(f"<span class='chip'>{_e(k.replace('_', ' '))}{' ×' + str(n) if n > 1 else ''}</span>"
                       for k, n in counts.items()) or "none"

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>LLM vs Rules</title><style>{PAGE_CSS}</style></head><body><main>
{form}
<h1>LLM vs rules at t = {_e(t)} s</h1>
<div class="sub">Run {_e(with_llm.get('simulation_id'))} · {_e(with_llm.get('protocol'))} · range {_e(with_llm.get('comm_range_m'))} m ·
{_e(w.get('packets_delivered'))} of {_e(w.get('packets_sent'))} messages delivered in the last
{_e((w.get('end') or 0) - (w.get('start') or 0))} s ({_pct(w.get('pdr'))}) · problems: {problems}</div>
{verdict}

<h2>1. Decision</h2>
<p class="hint">Highlighted rows (≠) are where the two modes differ.</p>
<div class="card"><table><thead><tr><th></th>
<th class="head-llm">{_e(left_title)}</th><th class="head-rule">Without LLM (rule-based)</th></tr></thead>
<tbody>{rows}</tbody></table></div>

<h2>2. Flow by flow: which nodes should carry the messages</h2>
<p class="hint">The LLM chooses relay nodes itself; code then checks every link really exists and scores the
path the same way as the computed route (stable links, margin, few hops). Bold nodes are used only by that
route. "Weakest link" = predicted seconds until the route's weakest link drifts out of range.</p>
{_flow_table(with_llm)}

<h2>3. Node by node</h2>
<p class="hint">Which nodes each diagnosis blames, and which nodes each route depends on. ✓ = yes.</p>
{_node_table(with_llm, without_llm, L, R)}

<h2>4. Details</h2>
{_details(with_llm, left_title)}
{_details(without_llm, 'Without LLM (rule-based)')}
</main></body></html>"""


def ui_form(time: float | None, backend: str, start: float, end: float) -> str:
    """Small form shown at the top of the /ui page."""
    sel = lambda b: " selected" if b == backend else ""  # noqa: E731
    return f"""<form method="get" action="/ui">
<label>Time (s) <input type="number" name="time" min="{start:g}" max="{end:g}" step="1"
 value="{'' if time is None else f'{time:g}'}" required></label>
<label>Backend <select name="backend"><option value="dry_run"{sel('dry_run')}>dry_run (record only)</option>
<option value="ns3_rerun"{sel('ns3_rerun')}>ns3_rerun (test in ns-3)</option></select></label>
<button type="submit">Compare</button></form>"""


def render_ui_start(backend: str, start: float, end: float) -> str:
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'><title>LLM vs Rules</title>"
            f"<style>{PAGE_CSS}</style></head><body><main>{ui_form(None, backend, start, end)}"
            f"<h1>LLM vs rules</h1><div class='sub'>Pick a simulation time ({start:g}–{end:g} s).</div>"
            f"</main></body></html>")
