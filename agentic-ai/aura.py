"""
aura.py — Command-line entry point for the AURA-MANET agentic AI backend.

    python aura.py analyze --time 120                 # full pipeline, dry run
    python aura.py analyze --time 80 --backend ns3_rerun   # execute + verify in ns-3
    python aura.py monitor --step 1                   # anomaly timeline (fast)
    python aura.py compare --step 10 --backend ns3_rerun   # agentic vs baseline
    python aura.py rag-ingest                         # (re)build the FAISS index
    python aura.py rag-query "black hole attack"
    python aura.py serve --port 8000                  # FastAPI server
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")


def _print_analysis(r: dict) -> None:
    print(f"\n=== t={r['current_time']:g}s  run={r['simulation_id']}  protocol={r['protocol']} ===")
    for e in r.get("trace", []):
        print(f"  {e['agent']:<13}{e['summary']}")
    d = r.get("diagnosis") or {}
    if d.get("findings"):
        print("\nDiagnosis findings:")
        for f in d["findings"]:
            print(f"  - {f['fault']} (conf {f['confidence']:.2f}) nodes={f['affected_nodes']}")
        if d.get("sources"):
            print("  sources: " + ", ".join(s["chunk_id"] for s in d["sources"]))
        for n in d.get("notes", []):
            print(f"  note: {n}")
    a = r.get("recovery_action") or {}
    print(f"\nAction: {a.get('action_type')}  {json.dumps(a.get('parameters', {}))}")
    print(f"  {a.get('rationale', '')}")
    v = r.get("verification_result") or {}
    print(f"\nVerification: {v.get('status')} ({v.get('mode')})")
    for n in v.get("notes", []):
        print(f"  {n}")
    b = (r.get("baseline_action") or {}).get("action", {})
    print(f"\nRule-based baseline would choose: {b.get('action_type')} {json.dumps(b.get('parameters', {}))}")


def main() -> None:
    parser = argparse.ArgumentParser(description="AURA-MANET agentic AI")
    parser.add_argument("--data-dir", help="ns-3 output folder (default NS3_METRICS_DIR)")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("analyze", help="run the full agent graph at one time")
    a.add_argument("--time", type=float, required=True)
    a.add_argument("--backend", choices=["dry_run", "ns3_rerun"])
    a.add_argument("--no-llm", action="store_true", help="force rule-based diagnosis")
    a.add_argument("--json", action="store_true", help="print the full JSON state")

    m = sub.add_parser("monitor", help="anomaly timeline (monitoring agent only)")
    m.add_argument("--step", type=float, default=1.0)
    m.add_argument("--start", type=float)
    m.add_argument("--end", type=float)

    c = sub.add_parser("compare", help="agentic decisions vs rule-based baseline")
    c.add_argument("--step", type=float, default=10.0)
    c.add_argument("--start", type=float)
    c.add_argument("--end", type=float)
    c.add_argument("--backend", choices=["dry_run", "ns3_rerun"])
    c.add_argument("--llm", action="store_true", help="use the LLM (one call per time step)")
    c.add_argument("--out", help="write the full comparison JSON here")

    sub.add_parser("rag-ingest", help="(re)build the FAISS index")
    q = sub.add_parser("rag-query", help="search the knowledge base")
    q.add_argument("query")
    q.add_argument("-k", type=int, default=5)

    s = sub.add_parser("serve", help="start the FastAPI server")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")

    if args.cmd == "serve":
        import uvicorn

        uvicorn.run("api.server:app", host=args.host, port=args.port)
        return

    from config import get_settings
    from rag.store import Retriever

    cfg = get_settings()
    if args.cmd == "rag-ingest":
        r = Retriever.from_settings(cfg)
        r._ensure_embedder()
        print(f"Indexed {r.rebuild()} chunks with {r.embedder_name} -> {cfg.RAG_INDEX_DIR}")
        return
    if args.cmd == "rag-query":
        r = Retriever.from_settings(cfg)
        for ch in r.retrieve(args.query, args.k):
            print(f"\n[{ch.chunk_id}] score={ch.score:.3f} ({ch.source})\n{ch.text[:400]}")
        return

    from pipeline import AuraPipeline

    pipe = AuraPipeline(cfg)
    if args.cmd == "analyze":
        result = pipe.analyze(args.time, args.data_dir, args.backend, use_llm=not args.no_llm)
        if args.json:
            print(json.dumps({k: v for k, v in result.items() if k not in
                              {"mobility_history", "packets_snapshot", "mobility_snapshot"}},
                             indent=1, default=str))
        else:
            _print_analysis(result)
            print(f"\nSaved: {cfg.RESULTS_DIR / 'latest.json'}")
    elif args.cmd == "monitor":
        for row in pipe.monitor_sweep(args.data_dir, args.start, args.end, args.step):
            print(f"t={row['time']:6g}  {row['max_severity'] or '-':8s}  {', '.join(row['types']) or '-'}")
    elif args.cmd == "compare":
        res = pipe.compare_with_baseline(args.data_dir, args.start, args.end, args.step,
                                         args.backend, use_llm=args.llm)
        print(json.dumps(res["summary"], indent=1))
        if args.out:
            Path(args.out).write_text(json.dumps(res, indent=1, default=str))
            print(f"Full comparison written to {args.out}")


if __name__ == "__main__":
    main()
