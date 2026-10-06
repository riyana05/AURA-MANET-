# AURA-MANET

MANET (Mobile Ad Hoc Network) simulation with NS-3, plus a Qt 6 / C++ desktop app that visualizes
the simulation from the CSV files it exports.

## Repository layout

```
AURA-MANET-/
├── ns-3.48/                     NS-3 simulator source
│   ├── scratch/random-waypoint-manet.cc   the MANET scenario (20 nodes, AODV, random waypoint)
│   └── MetricsOutput/           CSVs written by the scenario
├── MANET-Visualizer/            Qt 6 visualizer (see its README for build steps)
└── docs/                        project specification and phase plan
```

## Workflow

```bash
# 1. Run the simulation (writes ns-3.48/MetricsOutput/*.csv)
cd ns-3.48
./ns3 configure --enable-examples
./ns3 run random-waypoint-manet

# 2. Copy the mobility CSV to the visualizer and run it
cp MetricsOutput/node_mobility.csv ../MANET-Visualizer/data/
cd ../MANET-Visualizer
cmake -S . -B build -DCMAKE_PREFIX_PATH="$(brew --prefix qt)"
cmake --build build -j
./build/MANETVisualizer
```

## Phases

The full specification is in [docs/Qt6_MANET_Visualizer_Phased_Prompts.md](docs/Qt6_MANET_Visualizer_Phased_Prompts.md).

1. ✅ CSV → node positions → moving nodes → Play/Pause → Reset → timeline
2. Communication range → dynamic links → node selection → node information
3. Packet animation → PDR → throughput → delay → performance graphs
4. Topology mode → heatmap → node failure → route visualization
5. (future) AI/RAG-based analysis of the simulation
