# MANET Visualizer (Qt 6)

Desktop app that plays back the node movement recorded by the NS-3 scenario in
[`../ns-3.48/scratch/random-waypoint-manet.cc`](../ns-3.48/scratch/random-waypoint-manet.cc).

**Current status: Phase 3.**
- Phase 1: moving nodes, Play / Pause / Reset, speed, timeline
- Phase 2: communication range, dynamic links, node selection, selected-node info panel
- Phase 3: packet animation, packet information, PDR, throughput, delay, performance graphs

## Build and run (macOS)

```bash
brew install qt cmake          # once (includes Qt Charts)

cd MANET-Visualizer
cmake -S . -B build -DCMAKE_PREFIX_PATH="$(brew --prefix qt)"
cmake --build build -j

./build/MANETVisualizer                        # opens data/node_mobility.csv
./build/MANETVisualizer /path/to/other.csv     # or any other mobility CSV
```

You can also load a file from the **Open CSV…** button.

## Updating the data

The app reads `data/node_mobility.csv`, plus `packets.csv` if it is in the same folder.
After re-running the NS-3 simulation, copy the new files over:

```bash
cp ../ns-3.48/MetricsOutput/node_mobility.csv ../ns-3.48/MetricsOutput/packets.csv data/
```

## CSV format

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
| `neighbor_count`, `neighbors` | Nodes NS-3 found within its 100 m range (quoted list) | no, links are computed from X/Y instead (see below) |

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

## Phase 2: links and node information

- **Communication range** is set in the left panel (default 100 m, the range used by the NS-3
  scenario). Two nodes are linked when `distance(A, B) <= range`, which is the same rule the NS-3 script uses.
  The range is stored in `SimulationEngine`, which provides `linksAt(time)` and `neighborsAt(node, time)`.
- **Why not use the CSV `neighbors` column?** It only exists at whole-second timestamps and only for
  100 m. Computing links from the interpolated positions keeps them in step with the moving nodes and
  lets you change the range. At 100 m and whole-second times the computed neighbours match the CSV
  column exactly (checked for all 6,020 rows).
- **Links** are drawn with two `QGraphicsPathItem`s (one for all links, one in amber for links of the
  selected node). Both paths are rebuilt every time the simulation time or range changes, so links
  appear and disappear as nodes move.
- **Range circles** (`QGraphicsEllipseItem`, one per node) show around the selected node. Tick
  *Show range of every node* to show all of them.
- **Selection:** click a node to select it (a white ring is drawn around it). Click empty space to deselect. The
  right panel shows the node's ID, X/Y (metres), simulation time, neighbour count, neighbour IDs and range,
  and updates live while the simulation plays.

## Phase 3: packets and network metrics

### packets.csv

Written by the NS-3 scenario at the end of the run, one row per application packet:

```
packet_id,flow_id,source,destination,send_time,receive_time,delay_ms,size_bytes,status
40,0,1,4,1.128000,3.139606,2011.606,1024,received
160,0,1,4,1.384000,,,1024,lost
```

| Column | Meaning |
|---|---|
| `packet_id` | ns-3 packet UID (unique; copies of a packet keep it, so sent and received packets can be matched) |
| `flow_id` | Which of the UDP flows (0 to flows-1) the packet belongs to |
| `source`, `destination` | Node IDs of the flow's sender and receiver |
| `send_time` | When the OnOff application sent it (s, microsecond precision) |
| `receive_time` | When the PacketSink received it (s); empty if lost |
| `delay_ms` | `receive_time − send_time` in ms (the GUI recomputes it from the two times) |
| `size_bytes` | Application payload size (1024) |
| `status` | `received` or `lost` (not delivered before the simulation ended) |

Each row is an event pair (send + receive), not a periodic snapshot.

### How the metrics are computed (`PacketMetrics`)

All values at time *t* come from `packets.csv`:

| Metric | Definition |
|---|---|
| Sent / Received | packets with `send_time ≤ t` / `receive_time ≤ t` |
| Lost | packets sent by *t* that never arrive |
| PDR | `received / sent × 100` (packets still in flight count as not yet received) |
| Throughput | payload bytes received in `(t − 1 s, t]` × 8 / 1000 → kbps |
| Average delay | mean `receive_time − send_time` of all packets delivered by *t* |
| Delay graph | mean delay of the packets delivered during each second |

Sorted send/receive times plus running totals mean each value needs only a binary search.
The GUI's values match NS-3's own `network_metrics.csv` for every second of the run.

`PacketMetrics` is a new class so that `SimulationEngine` stays focused on time and positions.

### Packet animation

Each packet is drawn as a dot moving from its source to its destination, using the nodes' positions at
the current time. Delivered packets are green and lost packets are red. A lost packet only gets halfway, then disappears.
Most real delays are a few milliseconds, far too short to see, so a dot travels for
`max(real delay, 0.4 s × playback speed)` of simulation time. Packets that waited a long time (AODV
buffers packets for up to several seconds while it searches for a route) visibly take longer. The CSV records only the endpoints, not the hops in between, so
dots move on a straight line.

The **Packets: ON / OFF** button in the left panel (keyboard shortcut **P**) turns the animation on or off.
Metrics and graphs keep updating either way.

### Graphs

Three Qt Charts line graphs (PDR, throughput, per-second delay) with one point per simulation second.
They fill in up to the current time while playing and follow the timeline slider.
