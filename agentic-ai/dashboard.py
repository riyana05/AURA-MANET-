"""
dashboard.py — "How the AI helps" dashboard for one ns-3 run.

    python aura.py dashboard                 # analyse the run, open results/dashboard.html
    python aura.py dashboard --step 5 --llm  # decisions every 5 s, LLM at every decision

The page shows:

* delivery rate (PDR) over time for the real run and, for each fix the AI
  executed in ns-3, the same run with that fix applied;
* when the monitoring agent detected problems, and how severe they were;
* every decision the AI took, coloured by its verified outcome;
* for a selected decision: the network map (groups, failing flows, proposed
  route, links a stronger radio would add) and the step-by-step reasoning
  (what was detected -> diagnosis -> route search -> decision -> ns-3 check).

Everything shown comes from the CSVs or from the agents' outputs; nothing is
estimated for display.  The page is a single self-contained HTML file.
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path

from agents.monitoring_agent import detect_anomalies
from data_loader import build_state, load_dataset
from evidence import window_metrics
from schemas import SEVERITY_RANK

logger = logging.getLogger(__name__)


def _round(v, n=4):
    return None if v is None else round(float(v), n)


def _decision_payload(res: dict) -> dict:
    topo = res.get("topology") or {}
    action = res.get("recovery_action") or {}
    params = action.get("parameters") or {}
    extra_links: list[list[int]] = []
    if action.get("action_type") == "increase_tx_range" and topo.get("nodes"):
        old_r = float(res.get("comm_range_m") or 100.0)
        new_r = float(params.get("range_m") or old_r)
        nodes = topo["nodes"]
        for i, a in enumerate(nodes):
            for b in nodes[i + 1:]:
                d = math.dist((a["x"], a["y"]), (b["x"], b["y"]))
                if old_r < d <= new_r:
                    extra_links.append([a["id"], b["id"]])
    d = res.get("diagnosis") or {}
    ver = res.get("verification_result") or {}
    return {
        "time": res["current_time"],
        "anomalies": [{k: a.get(k) for k in ("type", "severity", "description", "flow_id", "affected_nodes")}
                      for a in res.get("anomalies", [])],
        "window": res.get("window_metrics"),
        "diagnosis": {k: d.get(k) for k in ("method", "root_cause", "recommended_action", "confidence",
                                            "reasoning", "notes", "fact_check", "llm_call")}
        | {"findings": [{k: f.get(k) for k in ("fault", "confidence", "affected_nodes", "evidence")}
                        for f in d.get("findings", [])],
           "sources": [{"chunk_id": s["chunk_id"], "text": s["text"][:260]} for s in d.get("sources", [])]},
        "routes": [{k: r.get(k) for k in ("flow_id", "source", "destination", "connected", "window_pdr")}
                   | {"best": (r.get("best") or {}).get("path"), "candidates": len(r.get("candidates", []))}
                   for r in res.get("candidate_routes", [])],
        "security": {k: (res.get("security_report") or {}).get(k) for k in ("risk_level", "notes")},
        "action": {k: action.get(k) for k in ("action_type", "parameters", "rationale", "alternatives", "flow_id")},
        "validation": {k: (res.get("validation") or {}).get(k) for k in ("valid", "executable", "errors", "warnings")},
        "execution": {k: (res.get("execution_result") or {}).get(k) for k in ("status", "executed", "details")},
        "verification": {k: ver.get(k) for k in ("status", "mode", "before", "after", "notes")},
        "what_if": res.get("recommendation_check") or {},
        "baseline": ((res.get("baseline_action") or {}).get("action") or {}).get("action_type"),
        "topology": {
            "nodes": [{k: n[k] for k in ("id", "x", "y", "degree")} for n in topo.get("nodes", [])],
            "links": [[link["a"], link["b"]] for link in topo.get("links", [])],
            "components": topo.get("components", []),
            "isolated": topo.get("isolated_nodes", []),
            "broken": topo.get("broken_links", []),
        },
        "extra_links": extra_links,
    }


def collect(pipe, data_dir=None, step: float = 10.0, backend: str = "ns3_rerun",
            use_llm: bool = False, focus: float | None = None) -> dict:
    """Run the agents over the whole run and gather everything the page needs."""
    cfg = pipe.cfg
    ds = pipe.dataset(data_dir)
    w = cfg.FLOW_WINDOW_S

    timeline = []
    for t in ds.times:
        st = build_state(ds, t, cfg)
        anomalies, _ = detect_anomalies(st, cfg)
        sev = max((a.severity for a in anomalies), key=SEVERITY_RANK.get, default=None)
        timeline.append({"t": t, "pdr": _round(st["window_metrics"]["pdr"]),
                         "severity": sev, "types": sorted({a.type for a in anomalies})})

    if focus is None:  # worst delivery moment (ignoring the start-up seconds)
        cands = [p for p in timeline if p["pdr"] is not None and p["t"] >= ds.start_time + w]
        focus = min(cands, key=lambda p: p["pdr"])["t"] if cands else ds.start_time
    times, t = [], ds.start_time
    while t <= ds.end_time + 1e-9:
        times.append(round(t, 6))
        t += step
    times = sorted(set(times) | {float(focus)})

    decisions, fixes = [], {}
    for t in times:
        llm_here = use_llm or t == focus
        res = pipe.analyze(t, data_dir, backend, use_llm=llm_here, save=False)
        decisions.append(_decision_payload(res))
        ex = res.get("execution_result") or {}
        if ex.get("executed") and ex.get("output_dir") and ex["output_dir"] not in fixes:
            p = res["recovery_action"]["parameters"]
            label = (f"With fix: radio range {p.get('range_m'):g} m"
                     if res["recovery_action"]["action_type"] == "increase_tx_range"
                     else f"With fix: {p.get('protocol')} routing")
            fixes[ex["output_dir"]] = label

    fix_series = []
    for out_dir, label in list(fixes.items())[:2]:  # at most 2 extra lines (3 series total)
        alt = load_dataset(Path(out_dir))
        fix_series.append({
            "label": label,
            "points": [_round(window_metrics(alt.packets, t - w, t, deliver_by=t).pdr) for t in ds.times],
        })

    return {
        "run": {"id": ds.data_dir.parent.name if ds.data_dir.name == "MetricsOutput" else ds.data_dir.name,
                "dir": str(ds.data_dir), "meta": ds.meta, "protocol": ds.protocol,
                "range": ds.comm_range(cfg.COMM_RANGE_M), "start": ds.start_time, "end": ds.end_time,
                "window_s": w},
        "llm": getattr(pipe.llm, "name", None),
        "llm_everywhere": use_llm,
        "backend": backend,
        "focus": focus,
        "times": ds.times,
        "timeline": timeline,
        "fixes": fix_series,
        "decisions": decisions,
    }


def write_dashboard(data: dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, default=str).replace("</", "<\\/")
    path.write_text(TEMPLATE.replace("__DATA__", payload))
    return path


TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AURA-MANET AI Dashboard</title>
<style>
:root{color-scheme:light;--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;
--grid:#e1e0d9;--axis:#c3c2b7;--border:rgba(11,11,11,.10);
--s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;--other:#b5b3aa;
--good:#0ca30c;--warning:#fab219;--serious:#ec835a;--critical:#d03b3b;--goodtext:#006300}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])){color-scheme:dark;--page:#0d0d0d;
--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);
--s1:#3987e5;--s2:#d95926;--s3:#199e70;--other:#5a5955;--goodtext:#0ca30c}}
:root[data-theme="dark"]{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;
--grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);--s1:#3987e5;--s2:#d95926;--s3:#199e70;
--other:#5a5955;--goodtext:#0ca30c}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1240px;margin:0 auto;padding:20px 16px 48px}
h1{font-size:22px;margin:0}h2{font-size:15px;margin:0 0 2px}.sub{color:var(--ink2)}
.muted{color:var(--muted)}.card{background:var(--surface);border:1px solid var(--border);border-radius:12px;
padding:16px;margin-top:14px}
.tiles{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-top:14px}
@media(max-width:760px){.tiles{grid-template-columns:repeat(2,1fr)}}
.tile{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:12px 14px}
.tile .v{font-size:28px;font-weight:650;font-variant-numeric:tabular-nums;line-height:1.2}
.tile .l{color:var(--ink2);font-size:13px}
.legend{display:flex;flex-wrap:wrap;gap:6px 16px;margin:6px 0 4px;color:var(--ink2);font-size:13px;align-items:center}
.key{display:inline-flex;align-items:center;gap:6px}
.kline{width:18px;height:0;border-top:2px solid}.kdash{width:18px;height:0;border-top:2px dashed}
.ksq{width:10px;height:10px;border-radius:2px}.kdot{width:10px;height:10px;border-radius:50%}
svg{display:block;width:100%;height:auto;overflow:visible}
svg text{fill:var(--muted);font-size:11px}
.tip{position:fixed;pointer-events:none;background:var(--surface);border:1px solid var(--border);
border-radius:8px;padding:8px 10px;box-shadow:0 4px 18px rgba(0,0,0,.12);font-size:12px;display:none;
z-index:10;max-width:280px}
.tip .row{display:flex;align-items:center;gap:6px}.tip b{font-variant-numeric:tabular-nums}
.detail{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:14px}
@media(max-width:900px){.detail{grid-template-columns:1fr}}
.steps{display:flex;flex-direction:column;gap:10px}
.step{border:1px solid var(--border);border-radius:10px;padding:10px 12px}
.step h3{margin:0 0 4px;font-size:13px;display:flex;gap:8px;align-items:center}
.num{display:inline-grid;place-items:center;width:20px;height:20px;border-radius:50%;background:var(--ink);
color:var(--surface);font-size:11px;flex:none}
.chip{display:inline-block;padding:0 8px;border-radius:999px;border:1px solid var(--border);font-size:12px;
margin:2px 4px 2px 0;color:var(--ink2)}
.st{display:inline-flex;align-items:center;gap:5px;font-weight:600}
.bars{display:grid;grid-template-columns:auto 1fr auto;gap:4px 8px;align-items:center;font-size:12px;margin-top:6px}
.bar{height:10px;border-radius:0 4px 4px 0;background:var(--s1)}
table{border-collapse:collapse;width:100%;font-size:13px}th,td{text-align:left;padding:6px 8px;
border-bottom:1px solid var(--grid)}th{color:var(--muted);font-weight:600}td.n{text-align:right;
font-variant-numeric:tabular-nums}tbody tr{cursor:pointer}tbody tr:hover,tbody tr.sel{background:var(--grid)}
details summary{cursor:pointer;color:var(--ink2)}
.controls{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
button{font:inherit;border:1px solid var(--border);background:var(--surface);color:var(--ink);border-radius:8px;
padding:4px 10px;cursor:pointer}
label{color:var(--ink2);font-size:13px}
</style></head>
<body><main id="app"></main><div class="tip" id="tip"></div>
<script id="data" type="application/json">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('data').textContent);
const NS = 'http://www.w3.org/2000/svg';
const $ = (tag, attrs = {}, parent) => { const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) (k === 'text') ? e.textContent = v : e.setAttribute(k, v);
  if (parent) parent.appendChild(e); return e; };
const S = (tag, attrs = {}, parent) => { const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) (k === 'text') ? e.textContent = v : e.setAttribute(k, v);
  if (parent) parent.appendChild(e); return e; };
const pct = v => v == null ? '–' : (100 * v).toFixed(1) + '%';
const SEV_COLOR = {critical: 'var(--critical)', high: 'var(--serious)', medium: 'var(--warning)'};
const SEV_LABEL = {critical: 'Critical', high: 'High', medium: 'Medium', low: 'Low'};
const ACTION = {no_action: 'No change', wait_for_reconnection: 'Wait for reconnection', reroute_flow: 'Reroute a flow',
  increase_tx_range: 'Stronger radio (range)', switch_protocol: 'Switch routing protocol', isolate_node: 'Isolate node'};
const OUTCOME = d => { const v = d.verification.status, ex = d.execution.executed;
  if (ex && v === 'success') return ['Fix helped (verified)', 'var(--good)', '✓'];
  if (ex && v === 'degraded') return ['Fix made it worse', 'var(--critical)', '✗'];
  if (ex) return ['Fix tested: little change', 'var(--warning)', '≈'];
  if (['no_action', 'wait_for_reconnection'].includes(d.action.action_type)) return ['No change made', 'var(--muted)', '–'];
  return ['Advice only (not runnable in ns-3)', 'var(--other)', 'i']; };
const tip = document.getElementById('tip');
function showTip(ev, rows) { tip.replaceChildren(); for (const r of rows) { const row = $('div', {class: 'row'}, tip);
  if (r.color) $('span', {class: r.dash ? 'kdash' : 'kline', style: `border-color:${r.color}`}, row);
  if (r.value != null) $('b', {text: r.value}, row); $('span', {class: 'muted', text: r.label || ''}, row); }
  tip.style.display = 'block'; const x = Math.min(ev.clientX + 14, innerWidth - tip.offsetWidth - 8);
  tip.style.left = x + 'px'; tip.style.top = (ev.clientY + 14) + 'px'; }
function hideTip() { tip.style.display = 'none'; }

const app = document.getElementById('app');
let selected = D.decisions.findIndex(d => d.time === D.focus); if (selected < 0) selected = 0;

// ---------------------------------------------------------------- header + tiles
const hdr = $('div', {}, app);
$('h1', {text: 'How the AI is helping this network'}, hdr);
const m = D.run.meta;
$('div', {class: 'sub', text: `Run ${D.run.id}: ${m.nodes || '?'} devices, ${m.area || '?'} m area, speed ${m.speed || '?'}, `
  + `${D.run.protocol} routing, radio range ${D.run.range} m, ${m.flows || '?'} message flows, ${D.run.end - D.run.start} s.`}, hdr);
$('div', {class: 'muted', text: `Diagnosis: ${D.llm ? 'AI model ' + D.llm : 'rule-based (no LLM key)'}`
  + `${D.llm ? (D.llm_everywhere ? ' at every decision' : ' at the highlighted worst moment; rules elsewhere') : ''}. `
  + `Fixes ${D.backend === 'ns3_rerun' ? 'are tested by re-running ns-3 with the fix applied (same movement)' : 'are recorded only'}.`}, hdr);

const tl = D.timeline, nProblem = tl.filter(p => ['high', 'critical'].includes(p.severity)).length;
const executed = D.decisions.filter(d => d.execution.executed);
const helped = executed.filter(d => d.verification.status === 'success');
const gains = executed.map(d => (d.verification.after?.pdr ?? null) - (d.verification.before?.pdr ?? null))
  .filter(g => !isNaN(g));
const tiles = $('div', {class: 'tiles'}, app);
for (const [v, l] of [[`${nProblem} s`, `with serious problems detected (of ${tl.length} s)`],
  [D.decisions.length, 'moments the AI analysed'],
  [`${helped.length} / ${executed.length}`, 'fixes tested in ns-3 that helped'],
  [gains.length ? `+${(100 * gains.reduce((a, b) => a + b, 0) / gains.length).toFixed(0)} pts` : '–',
   'average delivery-rate gain from tested fixes']]) {
  const t = $('div', {class: 'tile'}, tiles); $('div', {class: 'v', text: v}, t); $('div', {class: 'l', text: l}, t); }

// ---------------------------------------------------------------- timeline chart
const tc = $('div', {class: 'card'}, app);
$('h2', {text: 'Delivery rate over time, problems found, and the AI’s decisions'}, tc);
$('div', {class: 'muted', text: `Share of messages delivered in the last ${D.run.window_s} s. Click a decision dot to see how the AI decided.`}, tc);
const lg = $('div', {class: 'legend'}, tc);
const SERIES = [{label: 'Actual run (no fix)', color: 'var(--s1)', points: tl.map(p => p.pdr)}]
  .concat(D.fixes.map((f, i) => ({label: f.label, color: `var(--s${i + 2})`, points: f.points, dash: true})));
for (const s of SERIES) { const k = $('span', {class: 'key'}, lg); $('span', {class: s.dash ? 'kdash' : 'kline', style: `border-color:${s.color}`}, k); $('span', {text: s.label}, k); }
for (const [sev, c] of [['critical', 'var(--critical)'], ['high', 'var(--serious)'], ['medium', 'var(--warning)']]) {
  const k = $('span', {class: 'key'}, lg); $('span', {class: 'ksq', style: `background:${c}`}, k); $('span', {text: `${SEV_LABEL[sev]} problem`}, k); }

const W = 1180, H = 330, L = 44, R = 12, T = 10, PH = 200, STRIP = 14, DOTS = 36;
const svg = S('svg', {viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': 'Delivery rate timeline'}, tc);
const t0 = D.run.start, t1 = D.run.end, X = t => L + (t - t0) / (t1 - t0 || 1) * (W - L - R), Y = v => T + (1 - v) * PH;
for (const v of [0, .25, .5, .75, 1]) { S('line', {x1: L, x2: W - R, y1: Y(v), y2: Y(v), stroke: v ? 'var(--grid)' : 'var(--axis)', 'stroke-width': 1}, svg);
  S('text', {x: L - 6, y: Y(v) + 4, 'text-anchor': 'end', text: `${v * 100}%`}, svg); }
for (let t = t0; t <= t1; t += 30) S('text', {x: X(t), y: T + PH + STRIP + DOTS + 30, 'text-anchor': 'middle', text: `${t}s`}, svg);
for (const s of SERIES) { let d = '', pen = false;
  s.points.forEach((v, i) => { if (v == null) { pen = false; return; } d += `${pen ? 'L' : 'M'}${X(D.times[i]).toFixed(1)},${Y(v).toFixed(1)}`; pen = true; });
  S('path', {d, fill: 'none', stroke: s.color, 'stroke-width': 2, 'stroke-linejoin': 'round', 'stroke-dasharray': s.dash ? '6 4' : ''}, svg); }
// problem strip
const sy = T + PH + 8, dw = (W - L - R) / tl.length;
S('text', {x: L - 6, y: sy + 11, 'text-anchor': 'end', text: 'Problems'}, svg);
tl.forEach(p => { if (SEV_COLOR[p.severity]) S('rect', {x: X(p.t) - dw / 2, y: sy, width: Math.max(dw - 1, 1), height: STRIP, fill: SEV_COLOR[p.severity]}, svg); });
// decision dots
const dy = sy + STRIP + 18; S('text', {x: L - 6, y: dy + 4, 'text-anchor': 'end', text: 'AI'}, svg);
const dotEls = [];
D.decisions.forEach((d, i) => { const [lab, col, sym] = OUTCOME(d); const g = S('g', {tabindex: 0, role: 'button', 'aria-label': `${d.time}s ${ACTION[d.action.action_type]}: ${lab}`, style: 'cursor:pointer'}, svg);
  S('circle', {cx: X(d.time), cy: dy, r: 12, fill: 'transparent'}, g);
  const ring = S('circle', {cx: X(d.time), cy: dy, r: 9, fill: 'none', stroke: 'var(--ink)', 'stroke-width': 2, opacity: 0}, g);
  const hollow = col === 'var(--muted)';  // "no change": ring, so it differs from "advice only" by shape too
  S('circle', {cx: X(d.time), cy: dy, r: hollow ? 5 : 6, fill: hollow ? 'var(--surface)' : col,
    stroke: hollow ? col : 'var(--surface)', 'stroke-width': 2}, g);
  dotEls.push(ring);
  const tipRows = () => [{value: `${d.time}s`, label: ACTION[d.action.action_type]}, {value: sym, label: lab}];
  g.addEventListener('pointermove', e => showTip(e, tipRows())); g.addEventListener('pointerleave', hideTip);
  g.addEventListener('focus', () => { const b = g.getBoundingClientRect(); showTip({clientX: b.right, clientY: b.top}, tipRows()); });
  g.addEventListener('blur', hideTip); g.addEventListener('click', () => select(i));
  g.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); select(i); } }); });
const dl = $('div', {class: 'legend'}, tc);
for (const [l, c] of [['Fix helped (verified)', 'var(--good)'], ['Fix tested: little change', 'var(--warning)'], ['Fix made it worse', 'var(--critical)'],
  ['Advice only (not runnable in ns-3)', 'var(--other)'], ['No change made', 'var(--muted)']]) {
  const k = $('span', {class: 'key'}, dl);
  $('span', {class: 'kdot', style: c === 'var(--muted)' ? `border:2px solid ${c};width:9px;height:9px` : `background:${c}`}, k);
  $('span', {text: l}, k); }
// crosshair
const cross = S('line', {y1: T, y2: T + PH, stroke: 'var(--axis)', 'stroke-width': 1, opacity: 0}, svg);
const hit = S('rect', {x: L, y: T, width: W - L - R, height: PH + STRIP + 8, fill: 'transparent'}, svg);
hit.addEventListener('pointermove', e => { const r = svg.getBoundingClientRect(); const sx = (e.clientX - r.left) * W / r.width;
  const i = Math.max(0, Math.min(tl.length - 1, Math.round((sx - L) / (W - L - R) * (tl.length - 1)))); const p = tl[i];
  cross.setAttribute('x1', X(p.t)); cross.setAttribute('x2', X(p.t)); cross.setAttribute('opacity', 1);
  showTip(e, [{value: `${p.t}s`, label: ''}, ...SERIES.map(s => ({value: pct(s.points[i]), label: s.label, color: s.color, dash: s.dash})),
    {value: p.severity ? SEV_LABEL[p.severity] : 'None', label: p.types.join(', ').replaceAll('_', ' ') || 'no problems'}]); });
hit.addEventListener('pointerleave', () => { cross.setAttribute('opacity', 0); hideTip(); });

// ---------------------------------------------------------------- detail
const det = $('div', {class: 'card'}, app);
const detTitle = $('h2', {}, det); const detSub = $('div', {class: 'muted'}, det);
const detGrid = $('div', {class: 'detail', style: 'margin-top:10px'}, det);
const mapBox = $('div', {}, detGrid), stepsBox = $('div', {class: 'steps'}, detGrid);

function drawMap(d) {
  mapBox.replaceChildren();
  const ctl = $('div', {class: 'controls'}, mapBox);
  const cbId = 'extra'; const cb = $('input', {type: 'checkbox', id: cbId}, ctl); cb.checked = d.extra_links.length > 0;
  $('label', {for: cbId, text: d.extra_links.length ? `Show ${d.extra_links.length} links a ${d.action.parameters.range_m} m radio would add` : 'No extra links proposed'}, ctl);
  if (!d.extra_links.length) cb.disabled = true;
  const legend = $('div', {class: 'legend'}, mapBox);
  const comps = d.topology.components, cOf = {}; comps.forEach((c, i) => c.forEach(n => cOf[n] = i));
  const colors = ['var(--s1)', 'var(--s2)', 'var(--s3)'];
  const big = comps.filter(c => c.length > 1);
  big.slice(0, 3).forEach((c, i) => { const k = $('span', {class: 'key'}, legend); $('span', {class: 'kdot', style: `background:${colors[i]}`}, k); $('span', {text: `Group ${i + 1} (${c.length} devices)`}, k); });
  if (big.length > 3 || comps.some(c => c.length === 1)) { const k = $('span', {class: 'key'}, legend); $('span', {class: 'kdot', style: 'background:var(--other)'}, k); $('span', {text: 'Smaller groups / cut-off devices'}, k); }
  for (const [l, c, dash] of [['Failing message flow', 'var(--critical)', true], ['AI’s proposed route', 'var(--good)', false], ['Link a stronger radio would add', 'var(--ink2)', true]]) {
    const k = $('span', {class: 'key'}, legend); $('span', {class: dash ? 'kdash' : 'kline', style: `border-color:${c}`}, k); $('span', {text: l}, k); }
  const nodes = d.topology.nodes, pos = {}; nodes.forEach(n => pos[n.id] = n);
  const xs = nodes.map(n => n.x), ys = nodes.map(n => n.y);
  const area = (D.run.meta.area || '').split('x').map(Number);
  const maxX = Math.max(area[0] || 0, ...xs), maxY = Math.max(area[1] || 0, ...ys), minX = Math.min(0, ...xs), minY = Math.min(0, ...ys);
  const MW = 520, MH = 520, P = 22, sc = Math.min((MW - 2 * P) / (maxX - minX || 1), (MH - 2 * P) / (maxY - minY || 1));
  const px = x => P + (x - minX) * sc, py = y => MH - P - (y - minY) * sc;
  const msvg = S('svg', {viewBox: `0 0 ${MW} ${MH}`, role: 'img', 'aria-label': `Network map at ${d.time} s`}, mapBox);
  S('rect', {x: px(minX), y: py(maxY), width: (maxX - minX) * sc, height: (maxY - minY) * sc, fill: 'none', stroke: 'var(--grid)'}, msvg);
  const extraG = S('g', {}, msvg);
  for (const [a, b] of d.extra_links) S('line', {x1: px(pos[a].x), y1: py(pos[a].y), x2: px(pos[b].x), y2: py(pos[b].y), stroke: 'var(--ink2)', 'stroke-width': 1.5, 'stroke-dasharray': '4 4'}, extraG);
  extraG.style.display = cb.checked ? '' : 'none'; cb.addEventListener('change', () => extraG.style.display = cb.checked ? '' : 'none');
  for (const [a, b] of d.topology.links) S('line', {x1: px(pos[a].x), y1: py(pos[a].y), x2: px(pos[b].x), y2: py(pos[b].y), stroke: 'var(--axis)', 'stroke-width': 1.5}, msvg);
  const failing = d.anomalies.filter(a => a.type === 'flow_outage');
  for (const a of failing) { const [s, t] = a.affected_nodes; if (!pos[s] || !pos[t]) continue;
    S('line', {x1: px(pos[s].x), y1: py(pos[s].y), x2: px(pos[t].x), y2: py(pos[t].y), stroke: 'var(--critical)', 'stroke-width': 1.5, 'stroke-dasharray': '2 5', opacity: .8}, msvg); }
  const routeNodes = d.action.action_type === 'reroute_flow' ? (d.action.parameters.path || []) : [];
  if (routeNodes.length > 1) S('polyline', {points: routeNodes.map(n => `${px(pos[n].x)},${py(pos[n].y)}`).join(' '), fill: 'none', stroke: 'var(--good)', 'stroke-width': 4, 'stroke-linecap': 'round', 'stroke-linejoin': 'round'}, msvg);
  const flowEnds = new Set(failing.flatMap(a => a.affected_nodes));
  for (const n of nodes) { const ci = cOf[n.id]; const bigIdx = big.indexOf(comps[ci]);
    const fill = bigIdx >= 0 && bigIdx < 3 ? colors[bigIdx] : 'var(--other)';
    const g = S('g', {}, msvg);
    S('circle', {cx: px(n.x), cy: py(n.y), r: 14, fill: 'transparent'}, g);
    S('circle', {cx: px(n.x), cy: py(n.y), r: 8, fill, stroke: flowEnds.has(n.id) ? 'var(--critical)' : 'var(--surface)', 'stroke-width': 2}, g);
    S('text', {x: px(n.x), y: py(n.y) - 12, 'text-anchor': 'middle', style: 'fill:var(--ink2);font-size:10px', text: n.id}, g);
    const flows = failing.filter(a => a.affected_nodes.includes(n.id)).map(a => a.flow_id);
    g.addEventListener('pointermove', e => showTip(e, [{value: `Device ${n.id}`, label: ''}, {value: n.degree, label: 'neighbours in range'},
      {value: bigIdx >= 0 && bigIdx < 3 ? `Group ${bigIdx + 1}` : (n.degree ? 'small group' : 'cut off'), label: ''},
      ...(flows.length ? [{value: flows.join(', '), label: 'failing flow(s)'}] : [])]));
    g.addEventListener('pointerleave', hideTip); }
}

function bars(box, before, after, labels) {
  if (!before || !after || before.pdr == null || after.pdr == null) return;
  const b = $('div', {class: 'bars'}, box);
  for (const [l, v, c] of [[labels[0], before.pdr, 'var(--s1)'], [labels[1], after.pdr, 'var(--s2)']]) {
    $('span', {text: l}, b); const track = $('div', {}, b); $('div', {class: 'bar', style: `width:${(100 * v).toFixed(1)}%;background:${c}`}, track);
    $('b', {text: pct(v)}, b); }
}

function step(n, title, body) { const s = $('div', {class: 'step'}, stepsBox); const h = $('h3', {}, s);
  $('span', {class: 'num', text: n}, h); $('span', {text: title}, h); body(s); return s; }

function drawSteps(d) {
  stepsBox.replaceChildren();
  step(1, 'Watcher: what went wrong', s => {
    if (!d.anomalies.length) { $('div', {class: 'muted', text: 'No problems detected at this moment.'}, s); return; }
    const counts = {}; d.anomalies.forEach(a => counts[a.type] = (counts[a.type] || 0) + 1);
    for (const [t, c] of Object.entries(counts)) $('span', {class: 'chip', text: `${t.replaceAll('_', ' ')}${c > 1 ? ' ×' + c : ''}`}, s);
    $('div', {text: `Delivered ${d.window.packets_delivered} of ${d.window.packets_sent} messages in the last ${D.run.window_s} s (${pct(d.window.pdr)}).`}, s); });
  step(2, `Doctor: why it happened (${d.diagnosis.method === 'llm_rag' ? 'AI + knowledge base' : d.diagnosis.method === 'rule_based' ? 'rules' : 'not needed'})`, s => {
    $('div', {}, s).appendChild(document.createTextNode(d.diagnosis.root_cause || '–'));
    if (d.diagnosis.reasoning && d.diagnosis.method === 'llm_rag') $('div', {class: 'muted', text: d.diagnosis.reasoning}, s);
    if (d.diagnosis.findings?.[0]?.evidence?.length) { const det = $('details', {}, s); $('summary', {text: 'Evidence used'}, det);
      const ul = $('ul', {}, det); d.diagnosis.findings[0].evidence.slice(0, 6).forEach(e => $('li', {text: e}, ul)); }
    if (d.diagnosis.sources?.length) { const det = $('details', {}, s); $('summary', {text: `Knowledge consulted (${d.diagnosis.sources.length})`}, det);
      d.diagnosis.sources.forEach(src => { const p = $('p', {}, det); $('b', {text: src.chunk_id + ': '}, p); p.appendChild(document.createTextNode(src.text + '…')); }); }
    const fc = d.diagnosis.fact_check;
    if (fc && Object.keys(fc).length) $('div', {class: 'st', style: `color:${fc.passed ? 'var(--goodtext)' : 'var(--critical)'}`,
      text: fc.passed ? '✓ Fact-check: the AI’s claims match the measured network' : '✗ Fact-check: the AI got some facts wrong (confidence lowered)'}, s);
    if (d.diagnosis.recommended_action) $('div', {class: 'muted', text: `Recommends: ${ACTION[d.diagnosis.recommended_action]}`}, s);
    const c = d.diagnosis.llm_call;
    if (c && c.model) $('div', {class: 'muted', text: `LLM call: ${c.model} answered in ${c.latency_s} s (${c.prompt_tokens} tokens in, ${c.completion_tokens} out)`}, s);
    else if (d.diagnosis.method === 'rule_based') $('div', {class: 'muted', text: (d.diagnosis.notes || []).find(n => n.includes('LLM')) || 'Rule-based diagnosis.'}, s); });
  step(3, 'Navigator: can messages go another way?', s => {
    if (!d.routes.length) { $('div', {class: 'muted', text: 'No failing flows to reroute.'}, s); return; }
    const cut = d.routes.filter(r => !r.connected).length, ok = d.routes.filter(r => r.best).length;
    $('div', {text: `${d.routes.length} struggling flows: ${cut} have no path at all (devices are in different groups), ${ok} have a safe alternative route.`}, s);
    d.routes.filter(r => r.best).slice(0, 3).forEach(r => $('div', {class: 'muted', text: `Flow ${r.flow_id}: ${r.best.join(' → ')}`}, s)); });
  step(4, 'Manager: the decision', s => {
    $('div', {class: 'st', text: ACTION[d.action.action_type]}, s);
    $('div', {class: 'muted', text: d.action.rationale}, s);
    if (d.baseline && d.baseline !== d.action.action_type) $('div', {class: 'muted', text: `A simple rule-based system would have chosen: ${ACTION[d.baseline]}.`}, s); });
  step(5, 'Checker: did it help?', s => {
    const [lab, col, sym] = OUTCOME(d);
    $('div', {class: 'st', style: `color:${col === 'var(--other)' || col === 'var(--muted)' ? 'var(--ink2)' : col}`, text: `${sym} ${lab}`}, s);
    const v = d.verification;
    if (v.mode === 'counterfactual_rerun') bars(s, v.before, v.after, ['Without fix', 'With fix']);
    else if (v.before && v.after) { $('div', {class: 'muted', text: 'Nothing was changed; this is how delivery evolved on its own:'}, s);
      bars(s, v.before, v.after, ['Before', 'After']); }
    const w = d.what_if;
    if (w && w.status === 'tested' && w.verification) { $('div', {class: 'muted', style: 'margin-top:6px', text: `The diagnosis suggested “${ACTION[w.recommended]}” instead; tested too:`}, s);
      if (w.execution.executed) bars(s, w.verification.before, w.verification.after, ['Without', 'With suggestion']);
      else $('div', {class: 'muted', text: 'Not runnable in ns-3.'}, s); } });
}

function select(i) {
  selected = i; const d = D.decisions[i];
  dotEls.forEach((r, j) => r.setAttribute('opacity', j === i ? 1 : 0));
  rows.forEach((r, j) => r.classList.toggle('sel', j === i));
  detTitle.textContent = `At ${d.time} s${d.time === D.focus ? ' (worst moment of the run)' : ''}`;
  detSub.textContent = 'Left: the network at this moment. Right: how the AI team reached its decision.';
  drawMap(d); drawSteps(d);
}

// ---------------------------------------------------------------- table view
const tcard = $('div', {class: 'card'}, app);
$('h2', {text: 'All decisions'}, tcard); $('div', {class: 'muted', text: 'Click a row to inspect it.'}, tcard);
const table = $('table', {}, tcard), thead = $('thead', {}, table), hr = $('tr', {}, thead);
for (const h of ['Time', 'Problems', 'Diagnosis', 'Decision', 'Outcome', 'Delivery before', 'after']) $('th', {text: h}, hr);
const tbody = $('tbody', {}, table), rows = [];
D.decisions.forEach((d, i) => { const r = $('tr', {}, tbody); rows.push(r); r.addEventListener('click', () => { select(i); det.scrollIntoView({behavior: 'smooth'}); });
  $('td', {class: 'n', text: `${d.time}s`}, r); $('td', {text: d.anomalies.length}, r);
  $('td', {text: d.diagnosis.root_cause || '–'}, r); $('td', {text: ACTION[d.action.action_type]}, r);
  $('td', {text: OUTCOME(d)[0]}, r);
  $('td', {class: 'n', text: pct(d.verification.before?.pdr)}, r); $('td', {class: 'n', text: pct(d.verification.after?.pdr)}, r); });
app.insertBefore(det, tcard);
select(selected);
</script></body></html>
"""
