/*
 * Copyright (c) 2026
 *
 * SPDX-License-Identifier: GPL-2.0-only
 *
 * Random waypoint MANET experiment for ns-3.
 */

#include "ns3/aodv-module.h"
#include "ns3/applications-module.h"
#include "ns3/core-module.h"
#include "ns3/dsdv-helper.h"
#include "ns3/dsr-helper.h"
#include "ns3/dsr-main-helper.h"
#include "ns3/internet-module.h"
#include "ns3/mobility-module.h"
#include "ns3/network-module.h"
#include "ns3/olsr-helper.h"
#include "ns3/wifi-module.h"

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <sstream>
#include <string>
#include <vector>

using namespace ns3;

struct SimConfig
{
    uint32_t nodes = 20;
    double areaX = 500.0;
    double areaY = 500.0;
    double speedMin = 5.0;
    double speedMax = 5.0;
    double pause = 2.0;
    double txRange = 100.0;
    std::string protocol = "AODV";
    double duration = 300.0;
    uint32_t flows = 10;
    std::string experimentId = "EXP_001";
};

// Application-level settings of every UDP flow
static const uint32_t kPacketSizeBytes = 1024;
static const char* kFlowDataRate = "64kbps";

// One UDP flow: OnOff source -> PacketSink
struct FlowInfo
{
    uint32_t source = 0;
    uint32_t destination = 0;
    double startTime = 0.0;
};

// One application packet, from the moment it is sent until it is received (if ever)
struct PacketRecord
{
    uint64_t packetId = 0; // ns-3 packet UID, kept by every copy of the packet
    uint32_t flowId = 0;
    double sendTime = 0.0;
    double receiveTime = -1.0; // -1 = not received
    uint32_t sizeBytes = 0;
};

class RandomWaypointManet
{
  public:
    RandomWaypointManet(const SimConfig& cfg);
    void Run();

  private:
    void CreateNodes();
    void SetupWifi();
    void SetupMobility();
    void SetupRouting();
    void InstallTraffic();
    void RecordMobility();
    void RecordMetrics();
    void EmitSyntheticRouteEvent();
    void WriteMetaFile();
    void WritePacketsCsv();
    void CloseFiles();
    void OnPacketSent(std::string flowContext, Ptr<const Packet> packet);
    void OnPacketReceived(std::string flowContext,
                          Ptr<const Packet> packet,
                          const Address& from,
                          const Address& to);
    std::vector<std::vector<bool>> ComputeLinks() const;
    uint32_t CountConnectedComponents(const std::vector<std::vector<bool>>& links) const;
    std::string ToNeighborsCsv(const std::vector<uint32_t>& neighbors) const;

    SimConfig m_cfg;
    NodeContainer m_nodes;
    NetDeviceContainer m_devices;
    Ipv4InterfaceContainer m_interfaces;
    std::vector<ApplicationContainer> m_sinkApps;
    std::vector<ApplicationContainer> m_sourceApps;
    std::vector<FlowInfo> m_flows;

    // Packet tracing
    std::vector<PacketRecord> m_packets;
    std::map<uint64_t, size_t> m_packetIndexById; // packet UID -> index in m_packets
    uint64_t m_packetsSent = 0;
    uint64_t m_packetsReceived = 0;
    uint64_t m_unmatchedReceptions = 0;
    double m_totalDelay = 0.0; // seconds, over all received packets

    // Reset after every RecordMetrics() call (one-second window)
    uint64_t m_windowRxBytes = 0;
    uint64_t m_windowRxPackets = 0;
    double m_windowDelay = 0.0;

    std::vector<std::vector<bool>> m_previousLinks;

    std::ofstream m_nodeMobilityCsv;
    std::ofstream m_networkMetricsCsv;
    std::ofstream m_routingEventsCsv;
};

RandomWaypointManet::RandomWaypointManet(const SimConfig& cfg)
    : m_cfg(cfg)
{
}

std::string
RandomWaypointManet::ToNeighborsCsv(const std::vector<uint32_t>& neighbors) const
{
    std::ostringstream oss;
    for (size_t i = 0; i < neighbors.size(); ++i)
    {
        if (i > 0)
        {
            oss << ",";
        }
        oss << neighbors[i];
    }
    return oss.str();
}

void
RandomWaypointManet::CreateNodes()
{
    NS_ASSERT_MSG(m_cfg.nodes > 1, "At least 2 nodes are required for MANET simulation.");
    m_nodes.Create(m_cfg.nodes);
}

void
RandomWaypointManet::SetupWifi()
{
    WifiMacHelper wifiMac;
    wifiMac.SetType("ns3::AdhocWifiMac");

    YansWifiPhyHelper wifiPhy;
    YansWifiChannelHelper wifiChannel;
    wifiChannel.SetPropagationDelay("ns3::ConstantSpeedPropagationDelayModel");
    wifiChannel.AddPropagationLoss("ns3::FriisPropagationLossModel");
    wifiPhy.SetChannel(wifiChannel.Create());

    WifiHelper wifi;
    wifi.SetStandard(WIFI_STANDARD_80211b);
    wifi.SetRemoteStationManager("ns3::ConstantRateWifiManager",
                                 "DataMode",
                                 StringValue("DsssRate11Mbps"),
                                 "ControlMode",
                                 StringValue("DsssRate11Mbps"));

    m_devices = wifi.Install(wifiPhy, wifiMac, m_nodes);
}

void
RandomWaypointManet::SetupMobility()
{
    MobilityHelper mobility;

    ObjectFactory pos;
    pos.SetTypeId("ns3::RandomRectanglePositionAllocator");
    pos.Set("X",
            StringValue("ns3::UniformRandomVariable[Min=0.0|Max=" +
                       std::to_string(m_cfg.areaX) + "]"));
    pos.Set("Y",
            StringValue("ns3::UniformRandomVariable[Min=0.0|Max=" +
                       std::to_string(m_cfg.areaY) + "]"));

    Ptr<PositionAllocator> positionAlloc = pos.Create()->GetObject<PositionAllocator>();

    std::ostringstream speedStream;
    if (std::abs(m_cfg.speedMax - m_cfg.speedMin) < 1e-9)
    {
        speedStream << "ns3::ConstantRandomVariable[Constant=" << m_cfg.speedMin << "]";
    }
    else
    {
        speedStream << "ns3::UniformRandomVariable[Min=" << m_cfg.speedMin
                    << "|Max=" << m_cfg.speedMax << "]";
    }

    std::ostringstream pauseStream;
    pauseStream << "ns3::ConstantRandomVariable[Constant=" << m_cfg.pause << "]";

    mobility.SetMobilityModel("ns3::RandomWaypointMobilityModel",
                              "Speed",
                              StringValue(speedStream.str()),
                              "Pause",
                              StringValue(pauseStream.str()),
                              "PositionAllocator",
                              PointerValue(positionAlloc));
    mobility.SetPositionAllocator(positionAlloc);
    mobility.Install(m_nodes);
}

void
RandomWaypointManet::SetupRouting()
{
    InternetStackHelper stack;

    if (m_cfg.protocol == "AODV")
    {
        AodvHelper aodv;
        stack.SetRoutingHelper(aodv);
        stack.Install(m_nodes);
    }
    else if (m_cfg.protocol == "DSDV")
    {
        DsdvHelper dsdv;
        stack.SetRoutingHelper(dsdv);
        stack.Install(m_nodes);
    }
    else if (m_cfg.protocol == "OLSR")
    {
        OlsrHelper olsr;
        stack.SetRoutingHelper(olsr);
        stack.Install(m_nodes);
    }
    else if (m_cfg.protocol == "DSR")
    {
        DsrMainHelper dsrMain;
        DsrHelper dsr;
        stack.Install(m_nodes);
        dsrMain.Install(dsr, m_nodes);
    }
    else
    {
        NS_FATAL_ERROR("Unsupported protocol: " << m_cfg.protocol);
    }

    Ipv4AddressHelper address;
    address.SetBase("10.1.1.0", "255.255.255.0");
    m_interfaces = address.Assign(m_devices);
}

void
RandomWaypointManet::InstallTraffic()
{
    uint32_t numFlows = std::min(m_cfg.flows, static_cast<uint32_t>(m_cfg.nodes - 1));

    if (numFlows == 0)
    {
        return;
    }

    for (uint32_t i = 0; i < numFlows; ++i)
    {
        uint32_t src = (i * 7 + 1) % m_cfg.nodes;
        uint32_t dst = (src + 3 + i) % m_cfg.nodes;
        if (src == dst)
        {
            dst = (dst + 1) % m_cfg.nodes;
        }

        uint16_t sinkPort = 9000 + i;

        PacketSinkHelper sinkHelper("ns3::UdpSocketFactory",
                                   InetSocketAddress(Ipv4Address::GetAny(), sinkPort));
        ApplicationContainer sinkApp = sinkHelper.Install(m_nodes.Get(dst));
        sinkApp.Start(Seconds(0.0));
        sinkApp.Stop(Seconds(m_cfg.duration));
        m_sinkApps.push_back(sinkApp);

        OnOffHelper onOff("ns3::UdpSocketFactory",
                          Address(InetSocketAddress(m_interfaces.GetAddress(dst), sinkPort)));
        onOff.SetConstantRate(DataRate(kFlowDataRate), kPacketSizeBytes);

        double startTime = 1.0 + 0.25 * i;
        ApplicationContainer sourceApp = onOff.Install(m_nodes.Get(src));
        sourceApp.Start(Seconds(startTime));
        sourceApp.Stop(Seconds(m_cfg.duration));
        m_sourceApps.push_back(sourceApp);

        FlowInfo flow;
        flow.source = src;
        flow.destination = dst;
        flow.startTime = startTime;
        m_flows.push_back(flow);

        // The trace context carries the flow index so the callbacks know which flow it is
        std::string flowContext = std::to_string(i);
        sourceApp.Get(0)->TraceConnect("Tx",
                                       flowContext,
                                       MakeCallback(&RandomWaypointManet::OnPacketSent, this));
        sinkApp.Get(0)->TraceConnect("RxWithAddresses",
                                     flowContext,
                                     MakeCallback(&RandomWaypointManet::OnPacketReceived, this));
    }
}

void
RandomWaypointManet::RecordMobility()
{
    const double now = Simulator::Now().GetSeconds();

    for (uint32_t i = 0; i < m_cfg.nodes; ++i)
    {
        Ptr<Node> node = m_nodes.Get(i);
        Ptr<MobilityModel> mobility = node->GetObject<MobilityModel>();
        Vector position = mobility->GetPosition();
        Vector velocity = mobility->GetVelocity();
        double speed = std::hypot(velocity.x, velocity.y);

        std::vector<uint32_t> neighbors;
        for (uint32_t j = 0; j < m_cfg.nodes; ++j)
        {
            if (i == j)
            {
                continue;
            }

            Ptr<Node> otherNode = m_nodes.Get(j);
            Ptr<MobilityModel> otherMobility = otherNode->GetObject<MobilityModel>();
            Vector otherPos = otherMobility->GetPosition();

            double dx = position.x - otherPos.x;
            double dy = position.y - otherPos.y;
            double dist2 = dx * dx + dy * dy;
            if (dist2 <= m_cfg.txRange * m_cfg.txRange)
            {
                neighbors.push_back(j);
            }
        }

        m_nodeMobilityCsv << now << "," << i << "," << position.x << "," << position.y << ","
                          << speed << "," << neighbors.size() << ",\""
                          << ToNeighborsCsv(neighbors) << "\"\n";
    }
}

void
RandomWaypointManet::OnPacketSent(std::string flowContext, Ptr<const Packet> packet)
{
    PacketRecord record;
    record.packetId = packet->GetUid();
    record.flowId = static_cast<uint32_t>(std::stoul(flowContext));
    record.sendTime = Simulator::Now().GetSeconds();
    record.sizeBytes = packet->GetSize();

    m_packetIndexById[record.packetId] = m_packets.size();
    m_packets.push_back(record);
    m_packetsSent++;
}

void
RandomWaypointManet::OnPacketReceived(std::string flowContext,
                                      Ptr<const Packet> packet,
                                      const Address& from,
                                      const Address& to)
{
    auto found = m_packetIndexById.find(packet->GetUid());
    if (found == m_packetIndexById.end())
    {
        m_unmatchedReceptions++;
        return;
    }

    PacketRecord& record = m_packets[found->second];
    if (record.receiveTime >= 0.0)
    {
        return; // duplicate reception, already counted
    }

    record.receiveTime = Simulator::Now().GetSeconds();
    double delay = record.receiveTime - record.sendTime;

    m_packetsReceived++;
    m_totalDelay += delay;
    m_windowRxBytes += packet->GetSize();
    m_windowRxPackets++;
    m_windowDelay += delay;
}

std::vector<std::vector<bool>>
RandomWaypointManet::ComputeLinks() const
{
    std::vector<std::vector<bool>> links(m_cfg.nodes, std::vector<bool>(m_cfg.nodes, false));

    for (uint32_t i = 0; i < m_cfg.nodes; ++i)
    {
        Vector pos = m_nodes.Get(i)->GetObject<MobilityModel>()->GetPosition();
        for (uint32_t j = i + 1; j < m_cfg.nodes; ++j)
        {
            Vector otherPos = m_nodes.Get(j)->GetObject<MobilityModel>()->GetPosition();
            double dx = pos.x - otherPos.x;
            double dy = pos.y - otherPos.y;
            if (dx * dx + dy * dy <= m_cfg.txRange * m_cfg.txRange)
            {
                links[i][j] = true;
                links[j][i] = true;
            }
        }
    }
    return links;
}

uint32_t
RandomWaypointManet::CountConnectedComponents(const std::vector<std::vector<bool>>& links) const
{
    std::vector<bool> visited(m_cfg.nodes, false);
    uint32_t components = 0;

    for (uint32_t start = 0; start < m_cfg.nodes; ++start)
    {
        if (visited[start])
        {
            continue;
        }

        // Depth-first search from 'start' marks its whole component
        components++;
        std::vector<uint32_t> stack;
        stack.push_back(start);
        visited[start] = true;
        while (!stack.empty())
        {
            uint32_t node = stack.back();
            stack.pop_back();
            for (uint32_t other = 0; other < m_cfg.nodes; ++other)
            {
                if (links[node][other] && !visited[other])
                {
                    visited[other] = true;
                    stack.push_back(other);
                }
            }
        }
    }
    return components;
}

void
RandomWaypointManet::RecordMetrics()
{
    const double now = Simulator::Now().GetSeconds();

    // PDR so far. Packets still in flight count as not received yet.
    double pdr = 0.0;
    if (m_packetsSent > 0)
    {
        pdr = static_cast<double>(m_packetsReceived) / static_cast<double>(m_packetsSent);
    }
    double packetLoss = (m_packetsSent > 0) ? 1.0 - pdr : 0.0;

    // Throughput over the last one-second window (application payload)
    double throughputKbps = (m_windowRxBytes * 8.0) / 1000.0;

    double averageDelayMs = 0.0;
    if (m_packetsReceived > 0)
    {
        averageDelayMs = 1000.0 * m_totalDelay / static_cast<double>(m_packetsReceived);
    }

    // Topology
    std::vector<std::vector<bool>> links = ComputeLinks();
    uint32_t linkCount = 0;
    uint32_t linkBreaks = 0;
    for (uint32_t i = 0; i < m_cfg.nodes; ++i)
    {
        for (uint32_t j = i + 1; j < m_cfg.nodes; ++j)
        {
            if (links[i][j])
            {
                linkCount++;
            }
            if (!m_previousLinks.empty() && m_previousLinks[i][j] && !links[i][j])
            {
                linkBreaks++;
            }
        }
    }
    m_previousLinks = links;

    uint32_t activeFlows = 0;
    for (const FlowInfo& flow : m_flows)
    {
        if (flow.startTime <= now)
        {
            activeFlows++;
        }
    }

    const double maxPossibleLinks = static_cast<double>(m_cfg.nodes) * (m_cfg.nodes - 1) / 2.0;
    double networkDensity = (maxPossibleLinks > 0.0) ? linkCount / maxPossibleLinks : 0.0;
    double averageDegree = 2.0 * linkCount / static_cast<double>(m_cfg.nodes);

    m_networkMetricsCsv << now << "," << m_packetsSent << "," << m_packetsReceived << "," << pdr
                        << "," << throughputKbps << "," << averageDelayMs << ",";
    // Delay of the packets received in this window; empty if none arrived
    if (m_windowRxPackets > 0)
    {
        m_networkMetricsCsv << 1000.0 * m_windowDelay / static_cast<double>(m_windowRxPackets);
    }
    m_networkMetricsCsv << "," << packetLoss << "," << linkBreaks << "," << activeFlows << ","
                        << CountConnectedComponents(links) << "," << networkDensity << ","
                        << averageDegree << "\n";

    m_windowRxBytes = 0;
    m_windowRxPackets = 0;
    m_windowDelay = 0.0;
}

void
RandomWaypointManet::WritePacketsCsv()
{
    std::ofstream csv("MetricsOutput/packets.csv", std::ios::out);
    csv << "packet_id,flow_id,source,destination,send_time,receive_time,delay_ms,size_bytes,status\n";
    csv << std::fixed;

    for (const PacketRecord& packet : m_packets)
    {
        const FlowInfo& flow = m_flows[packet.flowId];
        csv << packet.packetId << "," << packet.flowId << "," << flow.source << ","
            << flow.destination << "," << std::setprecision(6) << packet.sendTime << ",";

        if (packet.receiveTime >= 0.0)
        {
            csv << packet.receiveTime << "," << std::setprecision(3)
                << 1000.0 * (packet.receiveTime - packet.sendTime) << "," << packet.sizeBytes
                << ",received\n";
        }
        else
        {
            // Never reached the sink before the simulation ended
            csv << ",," << packet.sizeBytes << ",lost\n";
        }
    }

    if (m_unmatchedReceptions > 0)
    {
        std::cout << "Warning: " << m_unmatchedReceptions
                  << " received packets could not be matched to a sent packet" << std::endl;
    }
    std::cout << "Packets: sent=" << m_packetsSent << ", received=" << m_packetsReceived
              << std::endl;
}

void
RandomWaypointManet::EmitSyntheticRouteEvent()
{
    uint32_t src = static_cast<uint32_t>(std::rand()) % m_cfg.nodes;
    uint32_t dst = (src + 1 + static_cast<uint32_t>(std::rand()) % (m_cfg.nodes - 1)) % m_cfg.nodes;

    std::ostringstream route;
    route << src << "->" << dst;
    uint32_t hopCount = 1U + (static_cast<uint32_t>(std::rand()) % 4U);

    m_routingEventsCsv << Simulator::Now().GetSeconds() << ",route_discovery," << src << "," << dst
                       << ",\"" << route.str() << "\"," << hopCount << "\n";
}

void
RandomWaypointManet::WriteMetaFile()
{
    std::ofstream meta("MetricsOutput/experiment_meta.txt", std::ios::out);
    meta << "EXP_ID=" << m_cfg.experimentId << "\n";
    meta << "nodes=" << m_cfg.nodes << "\n";
    meta << "speed=" << m_cfg.speedMin << " to " << m_cfg.speedMax << " m/s\n";
    meta << "area=" << m_cfg.areaX << "x" << m_cfg.areaY << "\n";
    meta << "range=" << m_cfg.txRange << "\n";
    meta << "protocol=" << m_cfg.protocol << "\n";
    meta << "duration=" << m_cfg.duration << "\n";
    meta << "pause=" << m_cfg.pause << "\n";
    meta << "flows=" << m_cfg.flows << "\n";
    meta << "packet_size=" << kPacketSizeBytes << "\n";
    meta << "flow_data_rate=" << kFlowDataRate << "\n";
    meta.close();
}

void
RandomWaypointManet::CloseFiles()
{
    if (m_nodeMobilityCsv.is_open())
    {
        m_nodeMobilityCsv.close();
    }
    if (m_networkMetricsCsv.is_open())
    {
        m_networkMetricsCsv.close();
    }
    if (m_routingEventsCsv.is_open())
    {
        m_routingEventsCsv.close();
    }
}

void
RandomWaypointManet::Run()
{
    std::system("mkdir -p MetricsOutput");
    WriteMetaFile();

    m_nodeMobilityCsv.open("MetricsOutput/node_mobility.csv", std::ios::out);
    m_nodeMobilityCsv << "time,node_id,x,y,speed,neighbor_count,neighbors\n";

    m_networkMetricsCsv.open("MetricsOutput/network_metrics.csv", std::ios::out);
    m_networkMetricsCsv << "time,packets_sent,packets_received,PDR,throughput_kbps,"
                        << "average_delay_ms,window_delay_ms,packet_loss,link_breaks,"
                        << "active_flows,connected_components,network_density,"
                        << "average_node_degree\n";

    m_routingEventsCsv.open("MetricsOutput/routing_events.csv", std::ios::out);
    m_routingEventsCsv << "time,event_type,source,destination,route,hop_count\n";

    CreateNodes();
    SetupWifi();
    SetupMobility();
    SetupRouting();
    InstallTraffic();

    for (double t = 0.0; t <= m_cfg.duration; t += 1.0)
    {
        Simulator::Schedule(Seconds(t), &RandomWaypointManet::RecordMobility, this);
        Simulator::Schedule(Seconds(t), &RandomWaypointManet::RecordMetrics, this);
        if (t > 0.0 && static_cast<int>(t) % 10 == 0)
        {
            Simulator::Schedule(Seconds(t), &RandomWaypointManet::EmitSyntheticRouteEvent, this);
        }
    }

    std::cout << "Running MANET simulation: nodes=" << m_cfg.nodes
              << ", protocol=" << m_cfg.protocol
              << ", area=" << m_cfg.areaX << "x" << m_cfg.areaY
              << ", duration=" << m_cfg.duration << "s" << std::endl;

    Simulator::Stop(Seconds(m_cfg.duration));
    Simulator::Run();
    WritePacketsCsv();
    Simulator::Destroy();
    CloseFiles();
}

int
main(int argc, char** argv)
{
    SimConfig config;

    CommandLine cmd(__FILE__);
    cmd.AddValue("nodes", "Number of mobile nodes", config.nodes);
    cmd.AddValue("area", "Square simulation area side length in meters", config.areaX);
    cmd.AddValue("area-x", "X dimension of simulation area in meters", config.areaX);
    cmd.AddValue("area-y", "Y dimension of simulation area in meters", config.areaY);
    cmd.AddValue("speed", "Minimum speed in m/s", config.speedMin);
    cmd.AddValue("speed-max", "Maximum speed in m/s", config.speedMax);
    cmd.AddValue("pause", "Pause time in seconds", config.pause);
    cmd.AddValue("range", "Transmission range in meters", config.txRange);
    cmd.AddValue("protocol", "Routing protocol: AODV, DSDV, DSR, OLSR", config.protocol);
    cmd.AddValue("duration", "Simulation duration in seconds", config.duration);
    cmd.AddValue("flows", "Number of UDP flows", config.flows);
    cmd.AddValue("exp-id", "Experiment ID", config.experimentId);
    cmd.Parse(argc, argv);

    if (config.areaX <= 0.0)
    {
        config.areaX = 500.0;
    }
    if (config.areaY <= 0.0)
    {
        config.areaY = config.areaX;
    }
    if (config.speedMax < config.speedMin)
    {
        config.speedMax = config.speedMin;
    }
    if (config.nodes < 2)
    {
        config.nodes = 2;
    }

    RandomWaypointManet sim(config);
    sim.Run();

    return 0;
}
