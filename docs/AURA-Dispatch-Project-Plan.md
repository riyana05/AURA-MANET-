# AURA-Dispatch

**Resilient emergency dispatch that keeps working when the cellular network fails.**

A real-time, Uber-style dispatch platform for emergency responders (ambulance, fire, rescue). When
infrastructure fails, field units fall back to an ad hoc mesh network (MANET) and keep relaying
incidents peer-to-peer. When connectivity returns, they sync and reconcile automatically. The mesh
layer is driven by the existing **AURA-MANET** ns-3 simulation.

---

## Table of contents

1. [Problem and pitch](#1-problem-and-pitch)
2. [End-to-end flow](#2-end-to-end-flow)
3. [Architecture](#3-architecture)
4. [Backend services](#4-backend-services)
5. [MANET degraded mode](#5-manet-degraded-mode)
6. [Data model](#6-data-model)
7. [Key algorithms](#7-key-algorithms)
8. [Tech stack](#8-tech-stack)
9. [Repository layout](#9-repository-layout)
10. [Week-by-week plan](#10-week-by-week-plan)
11. [Metrics to measure](#11-metrics-to-measure)
12. [Demo script](#12-demo-script)
13. [Interview talking points](#13-interview-talking-points)
14. [Resume bullets](#14-resume-bullets)
15. [Pitfalls to avoid](#15-pitfalls-to-avoid)
16. [Stretch goals](#16-stretch-goals)
17. [References](#17-references)

---

## 1. Problem and pitch

In floods, earthquakes and cyclones, the first thing to fail is **cellular connectivity**, which
is exactly when dispatch matters most. Conventional dispatch systems and ride-hailing apps assume a
connection to a central server is always available.

AURA-Dispatch runs in two modes:

| Mode | Behaviour |
|---|---|
| **Online** | Centralized real-time dispatch: live tracking, priority triage, ETA-based optimal assignment. |
| **Degraded** | Field units lose the backend and form an ad hoc mesh. Incidents, locations and assignments are relayed hop-by-hop. Units with uplink act as gateways. On reconnect, offline logs sync and conflicts are resolved deterministically. |

**What makes it different:** it combines Uber-style system design (geo-indexing, real-time
matching, event-driven microservices) with distributed-systems depth (partition tolerance,
eventual consistency, logical clocks) and a networking layer (MANET routing) backed by ns-3
simulation data.

---

## 2. End-to-end flow

1. A citizen reports *"building collapse, 3 injured"* with GPS coordinates.
2. The **Incident service** creates the incident; the **Triage service** assigns priority P1–P4.
3. The **Dispatch engine** finds nearby candidate responders (H3 cells), ranks them by ETA,
   priority and capability, and assigns one atomically.
4. The responder receives the assignment over WebSocket and acknowledges it. Its live location
   streams to the control-room map.
5. **The cell tower in sector 4 fails.** Responders in that sector lose the backend.
6. Their agents switch to **mesh mode** and gossip updates peer-to-peer. A unit that still has
   uplink acts as a **gateway** to the backend.
7. A new P1 incident in sector 4 still reaches a responder over 3 mesh hops. If no backend is
   reachable, the nearest unit **self-dispatches**.
8. The tower comes back. Agents **sync their offline logs**, conflicts are resolved, and the
   control room sees the complete timeline.

---

## 3. Architecture

```
                   ┌──────────────── CONTROL ROOM UI (React + Map) ───────────────┐
                   │  live map · incident queue · responder status · network view │
                   └──────────────────────────────┬───────────────────────────────┘
                                                  │ WebSocket
┌────────────┐   REST    ┌──────────────┐  ┌──────┴───────┐
│ Citizen app│──────────►│ API Gateway  │─►│ Realtime Hub │  (WS fan-out, Redis pub/sub)
└────────────┘           └──────┬───────┘  └──────────────┘
                                │
      ┌───────────────┬─────────┴──────┬──────────────────┬───────────────────┐
      ▼               ▼                ▼                  ▼                   ▼
 Incident Svc    Triage Svc      Dispatch Engine    Location Svc         Sync Svc
 (state machine) (priority)      (matching, ETA)    (H3 index, Redis)    (offline reconcile)
      │               │                │                  │                   │
      └───────────────┴──────── Kafka (event bus) ────────┴───────────────────┘
                                       │
                       Postgres + PostGIS (source of truth)

 ─────────────────────────────── FIELD LAYER ───────────────────────────────
  Responder Agent ◄──mesh──► Responder Agent ◄──mesh──► Gateway Agent ──► backend
  (SQLite store, outbox,     (gossip relay,             (has uplink)
   gossip protocol)           store-and-forward)
                        ▲
                        │ link up/down decisions per timestep
             ns-3 Network Oracle (AURA-MANET scenario)
```

---

## 4. Backend services

### 4.1 Incident service
Owns the incident lifecycle as a state machine:

```
REPORTED → TRIAGED → DISPATCHED → ACKNOWLEDGED → EN_ROUTE → ON_SCENE → RESOLVED
                                  ↘ REASSIGNED (preemption / timeout)
```

- Every transition is published to Kafka as an event (**event sourcing**), giving a complete,
  replayable audit trail.
- Transitions are validated: illegal jumps are rejected.

### 4.2 Triage service
- Priority score from incident type, people affected, description keywords and waiting time.
- **Aging:** waiting time slowly raises the score so low-priority incidents are never starved.

### 4.3 Dispatch engine (core)
- **Candidate search:** H3 k-ring expansion around the incident cell until enough available
  responders are found.
- **Ranking:** `score = w1·ETA + w2·capabilityMatch + w3·responderLoad`
- **ETA:** OSRM road routing on OpenStreetMap data, not straight-line distance.
- **Atomic assignment:** Redis lock or Postgres `SELECT … FOR UPDATE SKIP LOCKED`, so a
  responder is never double-assigned.
- **Preemption:** if a P1 arrives and every suitable unit is busy, a unit on a P4 can be
  reassigned, and the P4 is re-queued.
- **Acknowledgement timeout:** if a responder doesn't acknowledge within N seconds, the engine
  reassigns.
- **Batch mode:** in mass-casualty events, incidents are assigned together using the
  **Hungarian algorithm** instead of greedily one by one.

### 4.4 Location service
- Responders ping every 2–3 seconds.
- Hot data is kept in Redis GEO or an in-memory H3 index, **not** written to Postgres on every
  ping. Only sampled trails are persisted.

### 4.5 Realtime hub
- WebSocket gateway backed by Redis pub/sub. It pushes incident, assignment and location
  updates to the control room and to responders.

### 4.6 Sync service
- Accepts offline event logs from field agents after reconnection, deduplicates and merges them,
  and resolves conflicts (see 5.4).

---

## 5. MANET degraded mode

### 5.1 Field agent
Each responder is a simulated process (Go agent in Docker) with:

| Component | Purpose |
|---|---|
| Local store (SQLite) | Incidents, assignments and peers the agent knows about |
| Outbox | Events not yet acknowledged by the backend |
| Gossip protocol | Periodically exchanges state with currently reachable neighbours |
| Store-and-forward | Holds messages until a neighbour or gateway appears (delay-tolerant networking) |
| Mode detector | Switches online ↔ mesh based on backend heartbeat |

### 5.2 ns-3 as the network oracle
The AURA-MANET scenario (`ns-3.48/scratch/random-waypoint-manet.cc`) already exports
`node_mobility.csv`, `packets.csv`, `network_metrics.csv` and `routing_events.csv`, and models
radio range.

- **Option A (start here): replay.** A link controller reads the ns-3 mobility/range data and, at
  each timestep, computes which agents are within radio range. It enables or blocks traffic
  between them (in-app link filter, Toxiproxy, or iptables).
- **Option B (advanced): real-time emulation.** Run ns-3 in real-time mode with TAP bridges so
  container traffic actually passes through simulated Wi-Fi and AODV.

### 5.3 Routing over the mesh
- **Baseline:** epidemic gossip (flood with TTL, dedup by message ID).
- **Comparison:** AODV-routed delivery from ns-3. Compare PDR, end-to-end delay and overhead using
  the existing AURA-MANET metrics pipeline.

### 5.4 Reconciliation after reconnect
- Every event carries `(event_id, incident_id, origin_unit, hlc, payload)`, where `hlc` is a
  **hybrid logical clock**.
- Incident states are **monotonic**: merging takes the furthest state (a simple CRDT-style
  max-merge).
- All events are **idempotent**: duplicates are dropped by `event_id`.
- **Conflict case:** the backend assigned Ambulance A while Ambulance B self-assigned offline
  through the mesh. Resolve deterministically: the unit that acknowledged first by HLC wins (tie
  → closer unit). The other unit is told to stand down, and both decisions are logged.

### 5.5 Offline self-dispatch
When a P1 arrives over the mesh and no backend is reachable:
1. Agents within N hops compute their own ETA.
2. The nearest available unit broadcasts a **claim** with its HLC timestamp.
3. Competing claims resolve by (ETA, HLC, unit ID). Losers back off.

This is a lightweight leader election, a practical taste of distributed consensus.

---

## 6. Data model

```sql
incidents(
  id UUID PK, type TEXT, priority SMALLINT, status TEXT,
  location GEOGRAPHY(Point), people_affected INT,
  reported_at TIMESTAMPTZ, updated_hlc BIGINT
)

responders(
  id UUID PK, type TEXT, capabilities TEXT[], status TEXT,
  last_location GEOGRAPHY(Point), last_seen TIMESTAMPTZ, mode TEXT  -- online | mesh
)

assignments(
  id UUID PK, incident_id UUID FK, responder_id UUID FK, status TEXT,
  assigned_at TIMESTAMPTZ, source TEXT  -- central | mesh
)

events(                       -- event store
  event_id UUID PK, aggregate_id UUID, type TEXT, payload JSONB,
  hlc BIGINT, origin_node TEXT, received_at TIMESTAMPTZ
)
```

---

## 7. Key algorithms

| Area | Algorithm / technique |
|---|---|
| Geo search | Uber H3 hex indexing, k-ring expansion |
| Routing / ETA | OSRM (contraction hierarchies), A* fallback |
| Prioritization | Priority queue with aging |
| Batch matching | Hungarian algorithm (min-cost assignment) |
| Concurrency | Distributed locks / row locks, idempotency keys |
| Mesh dissemination | Epidemic gossip with TTL + dedup; AODV comparison |
| Ordering | Hybrid logical clocks |
| Merge | Monotonic state CRDT (max-merge) |
| Offline election | Claim-based leader election by (ETA, HLC, ID) |

---

## 8. Tech stack

| Layer | Choice |
|---|---|
| Backend services | **Go** (alternative: Java Spring Boot) |
| Event bus | Kafka (or Redpanda, which is lighter) |
| Cache, geo, locks | Redis |
| Database | Postgres + PostGIS |
| Routing / ETA | OSRM + OpenStreetMap extract of your city |
| Geo index | Uber H3 |
| Field agents | Go + SQLite |
| Network simulation | **ns-3 (AURA-MANET)** |
| Frontend | React + Leaflet/Mapbox + WebSockets |
| Observability | Prometheus, Grafana, OpenTelemetry |
| Infrastructure | Docker Compose → Kubernetes (kind / k3s) |
| Load testing | k6 |
| CI | GitHub Actions (lint, unit + integration tests) |

---

## 9. Repository layout

```
aura-dispatch/
├── services/
│   ├── gateway/            API gateway
│   ├── incident/           incident lifecycle + event sourcing
│   ├── triage/             priority scoring
│   ├── dispatch/           matching engine
│   ├── location/           location ingest + H3 index
│   ├── realtime/           WebSocket hub
│   └── sync/               offline reconciliation
├── field-agent/            responder agent (gossip, outbox, SQLite)
├── network-oracle/         reads ns-3 output, controls agent links
├── simulator/              citizen + responder load generators
├── web/                    control-room React app
├── pkg/                    shared Go libs (events, hlc, h3 helpers)
├── deploy/
│   ├── docker-compose.yml
│   └── k8s/
├── loadtest/               k6 scripts
├── docs/                   architecture, ADRs, results
└── README.md
```

---

## 10. Week-by-week plan

| Week | Goal | Done when |
|---|---|---|
| 1 | Models, Postgres + PostGIS, REST APIs, incident state machine | Create/advance incidents via API with validated transitions |
| 2 | Location service (Redis + H3), responder simulator on OSM roads | 100 simulated units moving, queryable by area |
| 3 | Dispatch engine: ETA via OSRM, atomic assignment | Concurrency tests prove zero double-assignment |
| 4 | Kafka, event sourcing, realtime hub, control-room map | Live map updates end to end |
| 5 | Triage, aging, preemption, ack timeouts, Hungarian batch mode | Mass-casualty scenario dispatches correctly |
| 6 | **Field agents:** local store, outbox, gossip, store-and-forward | Messages propagate across agents with scripted links |
| 7 | **ns-3 oracle:** AURA-MANET mobility/range drives agent links; tower-failure scenario | Sector outage visible; mesh delivery works |
| 8 | **Sync:** HLC, merge, conflict resolution, offline self-dispatch | Reconnect produces a consistent timeline, conflicts logged |
| 9 | Load + chaos testing, Grafana dashboards | Numbers for latency, throughput, delivery under outage |
| 10 | README, diagrams, ADRs, results write-up, 3-minute demo video | Public repo ready to share |

**Minimum viable version (if short on time):** weeks 1–4, plus simplified 6–8 using a scripted
link schedule instead of ns-3. It's still a strong project.

**Build order tip:** start as a **modular monolith** and split into services in week 4.

---

## 11. Metrics to measure

**Online mode**
- Dispatch latency (incident created → responder assigned): p50 / p95 / p99
- Location ingest throughput (pings/sec)
- WebSocket fan-out latency
- Double-assignment count under concurrency (target: 0)

**Degraded mode**
- P1 delivery ratio vs % of infrastructure down (0–60%)
- Mesh end-to-end delay vs hop count
- Gossip overhead (messages per delivered event)
- Epidemic gossip vs AODV: PDR, delay, overhead
- Time to consistency after reconnect
- Number of conflicts and how each was resolved

---

## 12. Demo script (3 minutes)

1. **City view:** 50 responders moving, incidents appearing, each dispatched in milliseconds.
2. **Surge:** fire 1,000 simultaneous incidents. Grafana shows p99 dispatch latency and zero
   double-assignments.
3. **Click "Tower failure: Sector 4."** The sector greys out, agents switch to mesh mode, and mesh
   links are drawn live from ns-3 range data.
4. **New P1 in sector 4:** delivered over 3 hops; the nearest unit self-dispatches.
5. **Restore the tower:** offline logs sync, one conflict resolves on screen, and the full timeline
   appears.

---

## 13. Interview talking points

- **System design:** H3 vs geohash vs quadtree; WebSocket fan-out at scale; why locations aren't
  written to the database on every ping; hot-spot cells.
- **Concurrency:** preventing double-assignment; distributed locks vs DB row locks; idempotency.
- **Distributed systems:** event sourcing, eventual consistency, hybrid logical clocks,
  CRDT-style merges, CAP explained using this system.
- **Algorithms:** priority queue with aging, Hungarian algorithm, gossip convergence, A*/ETA.
- **Networking:** AODV vs epidemic routing, delay-tolerant networking, PDR/latency trade-offs,
  backed by real ns-3 data.
- **Trade-offs to defend:** why simulated agents instead of phones; why Kafka; why Go; why
  monolith-first.

---

## 14. Resume bullets

Replace the numbers with your measured results.

- Built a real-time emergency dispatch platform (Go, Kafka, Redis, PostGIS) with H3-indexed,
  ETA-based matching; dispatched **1,000 concurrent incidents at p99 < 100 ms** with zero
  double-assignments.
- Designed a partition-tolerant field layer in which responders fall back to a gossip-based ad hoc
  mesh during outages and reconcile state via hybrid logical clocks on reconnect.
- Integrated an ns-3 MANET simulation (AODV, random waypoint) as a network oracle; maintained
  **X% P1 incident delivery under a 40% infrastructure outage**.

---

## 15. Pitfalls to avoid

- **Don't build mobile apps.** Simulated agents plus a web dashboard are enough.
- **Don't start with microservices.** Monolith first, split later.
- **Don't run real Wi-Fi mesh on phones.** It isn't feasible here. ns-3-driven simulation is the
  right, defensible choice.
- **Don't skip tests on the dispatch engine.** Concurrency bugs are the whole point.
- **Measure everything.** Without numbers it's a demo; with numbers it's an engineering project.
- **Write ADRs** (short architecture decision records). They make interview answers easy.

---

## 16. Stretch goals

- ns-3 real-time emulation with TAP bridges (Option B)
- Multi-region backend with failover
- ML-based demand forecasting to pre-position responders
- SMS / USSD incident reporting for feature phones
- Hospital capacity awareness (route patients to hospitals with free beds)
- Replay mode: re-run any disaster scenario from the event store

---

## 17. References

- *Designing Data-Intensive Applications*, Martin Kleppmann
- Uber H3: https://h3geo.org
- OSRM: https://project-osrm.org
- Hybrid Logical Clocks, Kulkarni et al. (2014)
- Epidemic routing for partially-connected ad hoc networks, Vahdat & Becker (2000)
- AODV, RFC 3561
- ns-3 documentation: https://www.nsnam.org/documentation
- MIT 6.5840 Distributed Systems (labs and lecture notes)
