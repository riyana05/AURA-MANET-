# AURA-MANET Agentic AI

A multi-agent system that monitors the ns-3 MANET simulation, detects anomalies, diagnoses faults
with an LLM and retrieval-augmented generation (RAG), proposes routes and recovery actions, and
checks them. When the simulator can apply an action, the system executes it in ns-3 and verifies
the result.

It reads the CSVs that `ns-3.48/scratch/random-waypoint-manet.cc` already writes. The simulator and
the Qt visualizer are unchanged.

## Pipeline

```
ns-3 CSVs ─► Monitoring ─┬─► Diagnosis (LLM + RAG) ─┬─► Routing ─► Orchestrator ─► Validation
  (data_loader)          └─► Security & Energy  ────┘                                 │
                                                                                     ▼
          results/latest.json, FastAPI ◄── Baseline ◄── Verification ◄── Execution (dry run / ns-3)
```

| Agent | File | LLM? | What it does |
|---|---|---|---|
| Monitoring | `agents/monitoring_agent.py` | no | Applies threshold rules to windowed PDR, throughput and delay, link breaks, isolation, partitions, flow outages, and energy and forwarding when available |
| Diagnosis | `agents/diagnosis_agent.py` | **yes** | Retrieves knowledge with RAG, asks Groq for a structured diagnosis, then validates it. Falls back to rules when there is no key or the call fails |
| Security & Energy | `agents/security_agent.py` | no | Computes node trust (from forwarding counters) and energy, and rejects unsafe routes |
| Routing | `agents/routing_agent.py` | no | Finds the k shortest paths with NetworkX, scores them by hops, predicted link lifetime, range margin, trust and energy, and picks the best safe one |
| Orchestrator | `agents/orchestrator_agent.py` | no | Picks the least disruptive action that addresses the cause; takes the LLM's advice into account |
| Validation | `agents/action_validator.py` | no | Checks that nodes and links exist, parameters are in bounds, there are no unsafe relays, and whether the action can be executed |
| Execution | `agents/recovery_executor.py` | no | `dry_run` records the action; `ns3_rerun` re-runs ns-3 with the action applied |
| Verification | `agents/verification_agent.py` | no | Compares PDR, throughput and delay before and after the action |
| Baseline | `baseline.py` | no | A conventional rule-based system used for comparison |

The workflow is wired in `graph.py` with LangGraph. Diagnosis and Security run in parallel, and
Routing waits for both. If there are no anomalies, the LLM branch is skipped.

## Setup

```bash
cd agentic-ai
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # add GROQ_API_KEY to enable the LLM (optional)
python aura.py rag-ingest     # builds the FAISS index (downloads all-MiniLM-L6-v2 once)
```

If the model download fails with `401 ... token is expired`, an old Hugging Face token on your
machine is being sent. Run `huggingface-cli logout`, or prefix commands with
`HF_HUB_DISABLE_IMPLICIT_TOKEN=1 HF_TOKEN=`. If the model can't be loaded, the retriever falls back
to a weaker hashing embedder and logs a warning.

## Usage

```bash
python aura.py analyze --time 120                        # full pipeline, actions recorded only
python aura.py analyze --time 80 --backend ns3_rerun     # execute the action in ns-3 and verify
python aura.py analyze --time 120 --json                 # full state as JSON
python aura.py monitor --step 1                          # anomaly timeline (fast, no LLM)
python aura.py compare --step 10 --backend ns3_rerun --out results/compare.json
python aura.py rag-query "black hole attack detection"
python aura.py serve --port 8000                         # API, docs at http://127.0.0.1:8000/docs
```

Each `analyze` call writes `results/<run>/t<time>.json` and `results/latest.json` for the visualizer.

### API (for the Qt visualizer)

| Method | Path | Body / query |
|---|---|---|
| GET | `/health` | Shows the LLM, embedder and whether ns-3 re-runs are available |
| GET | `/run` | Times, nodes, protocol, data availability |
| GET | `/metrics` | `time` |
| GET | `/topology` | `time`. Links include `margin` and `lifetime_s` |
| GET | `/anomalies` | `time` |
| GET | `/anomalies/timeline` | `step`, `start`, `end` |
| POST | `/analyze` | `{"time": 120, "backend": "dry_run", "use_llm": true}` |
| POST | `/compare` | `{"step": 10, "backend": "ns3_rerun"}` |
| POST | `/rag/query` | `{"query": "...", "k": 5}` |
| POST | `/rag/rebuild` | Rebuilds the knowledge-base index |

Any endpoint also accepts `data_dir` to analyse a different ns-3 output folder.

### Example output (`analyze --time 80 --backend ns3_rerun`, real run, no LLM key)

```
monitoring   8 anomalies: flow_outage, low_pdr, network_partition
diagnosis    [rule_based] topology partition (conf 0.85) -> increase_tx_range
security     risk=unknown, suspicious=[], low_energy=[]
routing      8 affected flows, 0 with a safe alternative route
orchestrator increase_tx_range: 8 of 10 flows have no path at all; raising range 100 m -> 125 m
validation   valid=True executable=True
execution    executed: Counterfactual ns-3 run
verification success (counterfactual_rerun)
             PDR 0.304 -> 0.751 (+0.447) over (80, 100]s; same mobility trace
```

## Tests

```bash
cd agentic-ai && pytest -q          # 26 tests, about 10 s, offline (hashing embedder, fake LLM)
```

The tests cover:
- that the topology matches ns-3's own component and link-break counts for every second of the real run;
- that monitoring at time t never uses packets received after t;
- that LLM output is validated (unknown nodes and citations removed, unsupported claims capped);
- that the LLM failure fallback works;
- route safety, the validator, the severity gate, the full graph and every API endpoint;
- a real ns-3 re-run (skipped if ns-3 is not built).

## Honesty rules built into the code

- **No invented metrics.** Every number comes from the CSVs. Energy and trust are `null` unless
  `node_energy.csv` or `node_forwarding.csv` exist, and the Security agent then reports risk as
  `unknown`. The current ns-3 scenario exports neither file.
- `routing_events.csv` is ignored, because the scenario fills it with random events.
- **The LLM is advisory.** It never computes routes or metrics. Its output is checked against a
  Pydantic schema, unknown node IDs and citations are removed, and black-hole or energy findings
  without supporting data are capped at confidence 0.3. `isolate_node` is refused without
  forwarding evidence.
- **Recovery is "executed" only when ns-3 actually ran.** The `ns3_rerun` backend re-runs the
  scenario with the action (`--range` or `--protocol`) applied *for the whole run*, on the same
  mobility trace. It is a counterfactual experiment, not a change to a live network, and results
  are labelled `counterfactual_rerun`. `reroute_flow` and `isolate_node` cannot be executed,
  because the scenario has no per-flow routing control. They are validated and reported as
  advisory.
- Without execution, verification only shows how the network evolved by itself
  (`observation_only`) and never counts as a recovery.
- **Previous fault cases** (`rag/knowledge_base/fault_cases.jsonl`) are recorded only after an
  action was executed and verified.

## Agentic vs baseline (real run, every 10 s, `ns3_rerun`, rule-based diagnosis)

| | Agentic | Rule-based baseline |
|---|---|---|
| Network changes (out of 31 decisions) | 22 | 29 |
| Disruption score | 30 | 47 |
| Invalid actions | 0 | 0 |
| Executed and verified | 8 (7 success, 1 no improvement) | 18 (15 success, 3 no improvement) |
| Mean verified PDR change | +72 % | +56 % |

How to read this:
- The agentic system makes fewer and less disruptive changes. It waits out partitions that routing
  cannot fix.
- The baseline raises the radio range more often and gains PDR at those moments, but it pays in
  energy and interference, which this scenario does not measure.
- `reroute_flow`, the agentic system's most common choice, cannot be verified until the ns-3
  scenario supports per-flow routing.

## Extending the simulator (optional, future)

- **Forwarding/trust:** trace `Ipv4L3Protocol` `UnicastForward` and `Drop` per node and write
  `node_forwarding.csv` (`time,node_id,forwarded,dropped`, counts per interval).
- **Energy:** install `BasicEnergySource` and `WifiRadioEnergyModel` and write `node_energy.csv`
  (`time,node_id,remaining_energy_j`).
- **Real routing control:** add static or source routes, or node on/off switches, so
  `reroute_flow` and `isolate_node` become executable.
- **Live mode:** stream per-second rows from ns-3 (e.g. over a socket) into `build_state`. The
  agents work per time step, so they need no changes.
