# AURA-MANET

A Qt 6 / C++ desktop visualizer for MANET (Mobile Ad Hoc Network) simulations generated with NS-3.
The app reads the CSV files exported by the NS-3 simulation and animates the network.

The full specification and phase plan is in [docs/Qt6_MANET_Visualizer_Phased_Prompts.md](docs/Qt6_MANET_Visualizer_Phased_Prompts.md).

## Phases

1. CSV → node positions → moving nodes → Play/Pause → Reset → timeline
2. Communication range → dynamic links → node selection → node information
3. Packet animation → PDR → throughput → delay → performance graphs
4. Topology mode → heatmap → node failure → route visualization
5. (future) AI/RAG-based analysis of the simulation
