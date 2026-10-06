# MANET Visualizer (Qt 6)

Desktop app that plays back the node movement recorded by the NS-3 scenario in
[`../ns-3.48/scratch/random-waypoint-manet.cc`](../ns-3.48/scratch/random-waypoint-manet.cc).

**Current status: Phase 1:** moving nodes, Play / Pause / Reset, speed, timeline.

## Build and run (macOS)

```bash
brew install qt cmake          # once

cd MANET-Visualizer
cmake -S . -B build -DCMAKE_PREFIX_PATH="$(brew --prefix qt)"
cmake --build build -j

./build/MANETVisualizer                        # opens data/node_mobility.csv
./build/MANETVisualizer /path/to/other.csv     # or any other mobility CSV
```

You can also load a file from the **Open CSV…** button.

## Updating the data

The app reads `data/node_mobility.csv`. After re-running the NS-3 simulation, copy the new file over:

```bash
cp ../ns-3.48/MetricsOutput/node_mobility.csv data/
```

## CSV format (Phase 1)

```
time,node_id,x,y,speed,neighbor_count,neighbors
0,0,304.989,474.298,0,0,""
0,1,27.7028,28.1529,0,1,"7"
```

| Column | Meaning | Used in Phase 1 |
|---|---|---|
| `time` | Simulation time in seconds (decimals are fine) | yes |
| `node_id` | Integer node ID (`nodeId` / `node` also accepted) | yes |
| `x`, `y` | NS-3 position in metres, Y pointing up | yes |
| `speed` | Node speed in m/s | no |
| `neighbor_count`, `neighbors` | Nodes within transmission range (quoted list) | no (Phase 2) |

Columns are found by header name, so their order does not matter. Rows can be in any order.

## How it works

| Class | Job |
|---|---|
| `CsvLoader` | Reads the CSV and returns one `MobilitySample {time, nodeId, x, y}` per row. Handles quoted fields such as `"14,15"`. |
| `SimulationEngine` | Groups samples into one time-sorted track per node, holds the current time, runs a `QTimer` (~60 fps) for playback, and emits `timeChanged(time)`. |
| `NodeItem` | A `QGraphicsItem` that draws one coloured node with its ID. |
| `MainWindow` | Builds the interface, converts NS-3 metres to scene coordinates, and moves each `NodeItem` when `timeChanged` fires. |

- **Timestamps:** the real CSV times are used. Between two timestamps a node's position is linearly
  interpolated, so movement looks smooth. (NS-3 random-waypoint motion is a straight line between
  waypoints, so this matches the real path except at the moment a node turns.)
- **Playback:** every timer tick adds `real time elapsed × speed` to the simulation time. At the end it pauses.
- **Timeline:** the slider counts in milliseconds from the first timestamp. Dragging it sets the
  engine time. When the engine time changes, the slider follows (with signals blocked to avoid a loop).
- **Scaling:** `minX/maxX/minY/maxY` come from the CSV. One scale factor is used for both axes so
  distances are not distorted. `sceneY = padding + (maxY − y) × scale` flips the Y axis, because NS-3 Y
  points up and screen Y points down. The view keeps the whole area fitted when the window is resized.
