# MANET FAULT PATTERNS AND RECOVERY PLAYBOOK

This document maps the anomaly types produced by the AURA-MANET Monitoring Agent
to probable faults, the evidence that confirms or rules each fault out, and the
least disruptive recovery action. It is reference knowledge for the Diagnosis
Agent; it contains no measurements from any particular run.

## 1. Anomaly signatures and probable faults

### 1.1 flow_outage with endpoints NOT connected
Signature: a flow delivers zero packets in the window and the source and
destination are in different connected components of the current topology.
Probable fault: topology partition caused by mobility (no physical path exists).
Routing changes cannot help: no protocol can deliver across a partition. The
correct responses are to wait for nodes to move back into range (store and
forward if the application supports it), increase transmission range or power,
or add relay nodes. Evidence to check: component sizes, distance between the
nearest nodes of the two components, recent network_partition anomalies.

### 1.2 flow_outage with endpoints connected
Signature: zero delivery although a multi-hop path exists in the topology.
Probable faults, most likely first:
1. Stale route / route repair in progress. With AODV a broken next hop is only
   detected after MAC retries fail; RERR propagation and a new RREQ follow, and
   packets are buffered or dropped meanwhile. Expect this right after
   link_breaks involving nodes on the old path.
2. Path exists only briefly. If the weakest link of every available path has a
   short predicted lifetime, routes break before they are used.
3. Packet-dropping node (black hole / grey hole). Only diagnose this when
   per-node forwarding counters show a node receiving packets for forwarding
   and not forwarding them. Without forwarding data a black hole cannot be
   distinguished from route instability and must not be claimed.
Preferred action: reroute over the most stable path (longest minimum link
lifetime, fewest hops); isolate a node only with forwarding evidence.

### 1.3 low_pdr together with frequent link_breaks
Probable fault: mobility-induced route breaks. Reactive protocols (AODV, DSR)
spend time rediscovering routes after every break. In sparse networks this is
the dominant loss cause. Proactive protocols (OLSR, DSDV) keep routes ready but
their tables become stale between updates when nodes move quickly.

### 1.4 low_pdr without link_breaks or partitions
Probable faults: congestion and collisions (hidden terminals), queue overflow
at relays carrying several flows, or a packet-dropping node. Check whether
delay is also high (congestion) and whether forwarding data identifies a node.

### 1.5 high_delay
High mean delay in a reactive protocol usually means packets waited in the
route-discovery buffer: AODV queues packets while a RREQ is outstanding and
releases them when the RREP arrives, so delays of hundreds of milliseconds to
seconds appear right after route breaks. Persistent high delay with high
throughput suggests congestion instead. Long paths add per-hop MAC delay.

### 1.6 isolated_node
A node with zero neighbours cannot send, receive or relay. If it is the source
or destination of a flow, that flow is in outage until the node moves back into
range. Routing changes do not help; wait for reconnection or increase range.

### 1.7 network_partition
The connected component count increased. Flows whose endpoints are now in
different components fail regardless of protocol. The partition is often
transient under random-waypoint mobility; acting is only worthwhile if flows
are affected and the partition persists.

### 1.8 low_energy and forwarding_drops
Low residual energy predicts node failure; routes through such nodes should be
avoided. A high forwarding drop ratio at one node while neighbours forward
normally is the main evidence for black hole or grey hole behaviour.

## 2. Routing protocol behaviour under mobility

### 2.1 AODV (reactive)
Discovers routes on demand; low overhead when traffic is light. After a link
break it needs RERR + RREQ/RREP, so delivery dips and delay spikes follow link
breaks. Performs well in moderate mobility with few flows; suffers when links
break often.

### 2.2 OLSR (proactive, link state)
Maintains routes to all nodes using HELLO and topology control messages via
multipoint relays. Routes are available immediately (low delay), but control
overhead is constant and tables lag behind fast topology changes. Often gives
lower delay than AODV; in proactive protocols packets for unreachable
destinations are typically refused at the source instead of buffered.

### 2.3 DSDV (proactive, distance vector)
Periodic and triggered full-table updates with sequence numbers. Slow
convergence after link breaks; generally weaker than AODV and OLSR under high
mobility.

### 2.4 DSR (reactive, source routing)
Carries the full route in each packet and caches routes. Route caches become
stale under mobility, causing errors; header overhead grows with path length.

### 2.5 Comparing protocols fairly
When protocols are compared, the number of packets sent can differ (a source
may refuse to send when it has no route), so PDR, delivered throughput and delay
must be read together. Always compare over the same mobility trace and time
window.

## 3. Recovery actions (least to most disruptive)

1. no_action / wait_for_reconnection: right when the anomaly is transient,
   low severity, or caused by a partition that routing cannot fix.
2. reroute_flow: steer a flow over a more stable or trusted path. Requires
   routing control in the simulator or a source-routing protocol.
3. increase_tx_range: raise transmit power so links last longer and partitions
   heal. Costs energy and increases interference; use moderate steps.
4. switch_protocol: change the routing protocol network-wide. Disruptive; only
   justified when an anomaly pattern persists across the run.
5. isolate_node: exclude a node from routing. Only with direct evidence of
   misbehaviour (forwarding drops), never on suspicion alone.

## 4. Facts about the AURA-MANET ns-3 scenario

The scenario uses 802.11b ad hoc Wi-Fi at 11 Mbps, random waypoint mobility,
constant-rate UDP OnOff flows and a hard radio range enforced by
RangePropagationLossModel: two nodes can communicate exactly when their
distance is at most the configured range. Connectivity in the agents'
topology is computed with the same rule, so it matches the simulator. The
scenario does not model energy or export per-node forwarding counters, so
trust and energy cannot be assessed unless those exports are added.
