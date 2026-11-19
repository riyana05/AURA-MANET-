# AURA-MANET

MANET (Mobile Ad Hoc Network) simulation with NS-3, plus a Qt 6 / C++ desktop app that visualizes
the simulation from the CSV files it exports.

## Repository layout

```
AURA-MANET-/
├── ns-3.48/                     NS-3 simulator source
│   ├── scratch/random-waypoint-manet.cc   the MANET scenario (20 nodes, AODV, random waypoint)
│   └── MetricsOutput/           CSVs written by the scenario (mobility, packets, metrics)
├── MANET-Visualizer/            Qt 6 visualizer (see its README for build steps)
├── agentic-ai/                  multi-agent AI (LangGraph, Groq, FAISS RAG, FastAPI) over the ns-3 CSVs
└── docs/                        project specification and phase plan
```

## Workflow

```bash
# 1. Build ns-3 once (only the modules the scenario needs) and run the simulation
cd ns-3.48
./ns3 configure -d release --enable-modules="aodv;olsr;dsdv;dsr;wifi;mobility;applications;internet;flow-monitor;point-to-point" --disable-examples --disable-tests
./ns3 build
./ns3 run random-waypoint-manet          # writes MetricsOutput/*.csv

# 2. Copy the CSVs to the visualizer and run it
cp MetricsOutput/node_mobility.csv MetricsOutput/packets.csv ../MANET-Visualizer/data/
cd ../MANET-Visualizer
cmake -S . -B build -DCMAKE_PREFIX_PATH="$(brew --prefix qt)"
cmake --build build -j
./build/MANETVisualizer
```

### 3. Agentic AI analysis

```bash
cd ../agentic-ai
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                                   # add GROQ_API_KEY to enable the LLM (optional)
python aura.py analyze --time 80 --backend ns3_rerun   # diagnose, act in ns-3, verify
python aura.py serve                                   # API for the visualizer
```

See [agentic-ai/README.md](agentic-ai/README.md) for the agents, API, tests and limitations.

## Phases

The full specification is in [docs/Qt6_MANET_Visualizer_Phased_Prompts.md](docs/Qt6_MANET_Visualizer_Phased_Prompts.md).

1. ✅ CSV → node positions → moving nodes → Play/Pause → Reset → timeline
2. ✅ Communication range → dynamic links → node selection → node information
3. ✅ Packet animation → PDR → throughput → delay → performance graphs
4. Topology mode → heatmap → node failure → route visualization
5. ✅ Agentic AI: monitoring, LLM + RAG diagnosis, routing, security, orchestration, ns-3-verified recovery
