"""
server.py — FastAPI backend for the existing Qt visualizer (or any client).

Run:  python aura.py serve            (http://127.0.0.1:8000/docs for Swagger)

Endpoints
---------
GET  /ui?time=T&backend=B        HTML page: the moment analysed with vs without the LLM
GET  /health                     status, LLM / RAG / ns-3 availability
GET  /run                        run info: times, nodes, protocol, data availability
GET  /metrics?time=T             network_metrics row + windowed metrics at T
GET  /topology?time=T            nodes, links (margin, predicted lifetime), components
GET  /anomalies?time=T           monitoring agent only (fast, no LLM)
GET  /anomalies/timeline?step=S  anomaly summary for every S seconds
POST /analyze                    full agent graph for one time  {time, backend?, use_llm?}
POST /compare                    agentic vs rule-based baseline {step, backend?, start?, end?}
POST /rag/query                  {query, k}
POST /rag/rebuild                re-ingest the knowledge base

``data_dir`` can be passed to any endpoint to analyse another ns-3 output
folder; the default is ``NS3_METRICS_DIR``.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Literal

_AGENTIC_AI_DIR = Path(__file__).resolve().parent.parent
if str(_AGENTIC_AI_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENTIC_AI_DIR))

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import HTMLResponse  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from agents.monitoring_agent import detect_anomalies  # noqa: E402
from agents.recovery_executor import Ns3RerunExecutor  # noqa: E402
from data_loader import DataLoadError, build_state  # noqa: E402
from pipeline import AuraPipeline  # noqa: E402
from report import render_comparison, render_ui_start, ui_form  # noqa: E402
from topology import topology_from_state  # noqa: E402

logger = logging.getLogger(__name__)

Backend = Literal["dry_run", "ns3_rerun"]


class AnalyzeRequest(BaseModel):
    time: float
    backend: Backend | None = None
    use_llm: bool = True
    data_dir: str | None = None
    save: bool = True


class CompareRequest(BaseModel):
    step: float = Field(default=10.0, gt=0)
    start: float | None = None
    end: float | None = None
    backend: Backend | None = None
    use_llm: bool = False
    data_dir: str | None = None


class RagQuery(BaseModel):
    query: str = Field(min_length=3)
    k: int = Field(default=5, ge=1, le=20)


def create_app(pipeline: AuraPipeline | None = None) -> FastAPI:
    """App factory (tests pass a pipeline with fake LLM / hashing embedder)."""
    app = FastAPI(title="AURA-MANET Agentic AI", version="1.0.0")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    holder: dict[str, AuraPipeline] = {}

    def pipe() -> AuraPipeline:
        if "p" not in holder:
            holder["p"] = pipeline or AuraPipeline()
        return holder["p"]

    def state_at(time: float, data_dir: str | None):
        p = pipe()
        try:
            ds = p.dataset(data_dir)
            return ds, build_state(ds, time, p.cfg)
        except DataLoadError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/ui", response_class=HTMLResponse)
    def ui(time: float | None = None, backend: Backend = "dry_run", data_dir: str | None = None):
        """Browser page comparing the LLM and rule-based results for one time."""
        p = pipe()
        try:
            ds = p.dataset(data_dir)
        except DataLoadError as exc:
            raise HTTPException(404, str(exc)) from exc
        if time is None:
            return render_ui_start(backend, ds.start_time, ds.end_time)
        state_at(time, data_dir)  # validates the time
        with_llm, without_llm = p.analyze_both(time, data_dir, backend)
        form = ui_form(time, backend, ds.start_time, ds.end_time)
        return render_comparison(with_llm, without_llm, getattr(p.llm, "name", None), form)

    @app.get("/health")
    def health():
        p = pipe()
        return {
            "status": "ok",
            "llm": getattr(p.llm, "name", None),
            "rag_embedder": p.retriever.embedder_name if p.retriever else None,
            "ns3_rerun_available": Ns3RerunExecutor(p.cfg).ready(),
            "default_backend": p.cfg.EXECUTOR_BACKEND,
        }

    @app.get("/run")
    def run_info(data_dir: str | None = None):
        try:
            ds = pipe().dataset(data_dir)
        except DataLoadError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {
            "data_dir": str(ds.data_dir),
            "start_time": ds.start_time,
            "end_time": ds.end_time,
            "nodes": ds.node_ids,
            "protocol": ds.protocol,
            "comm_range_m": ds.comm_range(pipe().cfg.COMM_RANGE_M),
            "meta": ds.meta,
            "data_availability": ds.availability,
        }

    @app.get("/metrics")
    def metrics(time: float, data_dir: str | None = None):
        _, st = state_at(time, data_dir)
        return {"time": time, "network_metrics": st["raw_metrics"],
                "window_metrics": st["window_metrics"], "flow_stats": st["flow_stats"]}

    @app.get("/topology")
    def topology(time: float, data_dir: str | None = None):
        _, st = state_at(time, data_dir)
        return topology_from_state(st, pipe().cfg.LINK_LIFETIME_CAP_S).to_dict()

    @app.get("/anomalies")
    def anomalies(time: float, data_dir: str | None = None):
        _, st = state_at(time, data_dir)
        found, _ = detect_anomalies(st, pipe().cfg)
        return {"time": time, "anomalies": [a.model_dump() for a in found]}

    @app.get("/anomalies/timeline")
    def timeline(step: float = 1.0, start: float | None = None, end: float | None = None,
                 data_dir: str | None = None):
        if step <= 0:
            raise HTTPException(422, "step must be > 0")
        try:
            return {"timeline": pipe().monitor_sweep(data_dir, start, end, step)}
        except DataLoadError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post("/analyze")
    def analyze(req: AnalyzeRequest):
        state_at(req.time, req.data_dir)  # validates input, raises 404/422
        result = pipe().analyze(req.time, req.data_dir, req.backend, req.use_llm, req.save)
        return {k: v for k, v in result.items()
                if k not in {"mobility_history", "packets_snapshot", "mobility_snapshot"}}

    @app.post("/compare")
    def compare(req: CompareRequest):
        try:
            return pipe().compare_with_baseline(req.data_dir, req.start, req.end, req.step,
                                                req.backend, req.use_llm)
        except DataLoadError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post("/rag/query")
    def rag_query(req: RagQuery):
        p = pipe()
        if p.retriever is None:
            raise HTTPException(503, "RAG retriever not configured")
        return {"embedder": p.retriever.embedder_name,
                "results": [c.model_dump() for c in p.retriever.retrieve(req.query, req.k)]}

    @app.post("/rag/rebuild")
    def rag_rebuild():
        p = pipe()
        if p.retriever is None:
            raise HTTPException(503, "RAG retriever not configured")
        return {"chunks": p.retriever.rebuild(), "embedder": p.retriever.embedder_name}

    return app


app = create_app()
