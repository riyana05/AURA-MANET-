"""
config.py — Application configuration for the AURA-MANET agentic AI backend.

Settings are read from environment variables (and from ``agentic-ai/.env``
when present) via Pydantic BaseSettings.  Copy ``.env.example`` to ``.env``
and fill in your values.

Relative paths are resolved against the ``agentic-ai/`` directory, so the code
works no matter which directory it is launched from.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Directory that contains this file (…/agentic-ai/)
BASE_DIR: Path = Path(__file__).resolve().parent


class Settings(BaseSettings):
    """Runtime configuration loaded from environment / .env file."""

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------------------------------------------ #
    # Credentials / LLM                                                    #
    # ------------------------------------------------------------------ #

    GROQ_API_KEY: str = Field(
        default="",
        description=(
            "Groq API key.  Without it the Diagnosis agent falls back to its "
            "rule-based mode; everything else works unchanged."
        ),
    )
    LLM_MODEL: str = Field(default="llama-3.3-70b-versatile")
    LLM_TEMPERATURE: float = Field(default=0.0, ge=0.0, le=2.0)
    LLM_TIMEOUT_S: float = Field(default=60.0, gt=0.0)

    # ------------------------------------------------------------------ #
    # Data paths                                                           #
    # ------------------------------------------------------------------ #

    NS3_METRICS_DIR: Path = Field(
        default=Path("../ns-3.48/MetricsOutput"),
        description="Directory with the ns-3 CSV outputs analysed by the agents.",
    )
    DATA_DIR: Path = Field(
        default=Path("../MANET-Visualizer/data"),
        description="Visualizer data folder (copy of node_mobility / packets CSVs).",
    )
    RESULTS_DIR: Path = Field(
        default=Path("results"),
        description="Where analysis JSON files for the visualizer are written.",
    )

    # ------------------------------------------------------------------ #
    # Monitoring thresholds                                                #
    # ------------------------------------------------------------------ #

    PDR_THRESHOLD: float = Field(default=0.6, ge=0.0, le=1.0)
    THROUGHPUT_THRESHOLD_KBPS: float = Field(default=10.0, gt=0.0)
    DELAY_THRESHOLD_MS: float = Field(default=500.0, gt=0.0)
    LINK_BREAKS_THRESHOLD: int = Field(
        default=2, ge=0, description="Anomaly when link breaks in one second exceed this."
    )
    FLOW_WINDOW_S: float = Field(
        default=5.0, gt=0.0, description="Window for per-flow / windowed PDR."
    )
    MIN_PACKETS_FOR_RATIO: int = Field(
        default=20,
        ge=1,
        description="Minimum packets sent in the window before a PDR is judged.",
    )
    FLOW_OUTAGE_MIN_PACKETS: int = Field(
        default=10, ge=1, description="Packets a flow must send in the window to be an outage."
    )
    ISOLATION_WINDOW_S: float = Field(default=5.0, gt=0.0)
    ENERGY_THRESHOLD_J: float = Field(
        default=5.0, ge=0.0, description="Remaining energy below this is 'low' (if data exists)."
    )
    FORWARD_DROP_THRESHOLD: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description="Forwarding drop ratio above this marks a node suspicious (if data exists).",
    )

    # ------------------------------------------------------------------ #
    # Topology / routing                                                   #
    # ------------------------------------------------------------------ #

    COMM_RANGE_M: float = Field(
        default=0.0,
        ge=0.0,
        description="Radio range (m).  0 = read it from experiment_meta.txt (fallback 100).",
    )
    ROUTE_K_PATHS: int = Field(default=3, ge=1, le=10)
    ROUTE_MAX_FLOWS: int = Field(default=10, ge=1)
    LINK_LIFETIME_CAP_S: float = Field(default=60.0, gt=0.0)
    MIN_LINK_LIFETIME_S: float = Field(
        default=1.0, ge=0.0, description="Routes with a link predicted to break sooner are rejected."
    )
    TRUST_THRESHOLD: float = Field(default=0.7, ge=0.0, le=1.0)
    ROUTE_WEIGHT_HOPS: float = 0.30
    ROUTE_WEIGHT_LIFETIME: float = 0.30
    ROUTE_WEIGHT_MARGIN: float = 0.20
    ROUTE_WEIGHT_TRUST: float = 0.10
    ROUTE_WEIGHT_ENERGY: float = 0.10

    # ------------------------------------------------------------------ #
    # Orchestration / recovery                                             #
    # ------------------------------------------------------------------ #

    ACTION_MIN_SEVERITY: Literal["low", "medium", "high", "critical"] = Field(
        default="high", description="Below this severity the orchestrator takes no action."
    )
    MAX_TX_RANGE_M: float = Field(default=250.0, gt=0.0)
    TX_RANGE_STEP: float = Field(default=1.25, gt=1.0)
    ALLOWED_PROTOCOLS: list[str] = Field(default_factory=lambda: ["AODV", "OLSR", "DSDV", "DSR"])
    EXECUTOR_BACKEND: Literal["dry_run", "ns3_rerun"] = "dry_run"
    NS3_DIR: Path = Field(default=Path("../ns-3.48"))
    NS3_SCENARIO: str = "random-waypoint-manet"
    NS3_RUNS_DIR: Path = Field(default=Path("runs"))
    NS3_TIMEOUT_S: float = Field(default=600.0, gt=0.0)
    VERIFY_WINDOW_S: float = Field(default=20.0, gt=0.0)
    VERIFY_MIN_IMPROVEMENT: float = Field(
        default=0.05, ge=0.0, description="PDR gain (absolute) needed to call a recovery a success."
    )

    # ------------------------------------------------------------------ #
    # RAG                                                                  #
    # ------------------------------------------------------------------ #

    RAG_KB_DIR: Path = Field(default=Path("rag/knowledge_base"))
    RAG_INDEX_DIR: Path = Field(default=Path("rag/index"))
    RAG_EMBEDDER: Literal["sentence-transformers", "hashing"] = "sentence-transformers"
    RAG_EMBED_MODEL: str = "sentence-transformers/all-MiniLM-L6-v2"
    RAG_CHUNK_CHARS: int = Field(default=900, ge=200)
    RAG_CHUNK_OVERLAP: int = Field(default=150, ge=0)
    RAG_TOP_K: int = Field(default=5, ge=1, le=20)

    # ------------------------------------------------------------------ #
    # Validators / helpers                                                 #
    # ------------------------------------------------------------------ #

    @field_validator(
        "NS3_METRICS_DIR",
        "DATA_DIR",
        "RESULTS_DIR",
        "NS3_DIR",
        "NS3_RUNS_DIR",
        "RAG_KB_DIR",
        "RAG_INDEX_DIR",
    )
    @classmethod
    def _resolve_relative_to_base(cls, value: Path) -> Path:
        """Resolve relative paths against ``agentic-ai/`` instead of the CWD."""
        value = Path(value).expanduser()
        if not value.is_absolute():
            value = BASE_DIR / value
        return value.resolve()

    def groq_key(self) -> str | None:
        """Return the Groq API key, or ``None`` if it is not configured."""
        key = self.GROQ_API_KEY.strip()
        if not key or key == "your_groq_api_key_here":
            return None
        return key


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide :class:`Settings` instance (created on first use).

    Tests call ``get_settings.cache_clear()`` after changing the environment.
    """
    return Settings()
