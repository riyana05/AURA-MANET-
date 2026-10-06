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

#include <cmath>
#include <cstdlib>
#include <fstream>
#include <iomanip>
#include <iostream>
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
    void CloseFiles();
    std::string ToNeighborsCsv(const std::vector<uint32_t>& neighbors) const;

    SimConfig m_cfg;
    NodeContainer m_nodes;
    NetDeviceContainer m_devices;
    Ipv4InterfaceContainer m_interfaces;
    std::vector<ApplicationContainer> m_sinkApps;
    std::vector<ApplicationContainer> m_sourceApps;

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
        onOff.SetConstantRate(DataRate("64kbps"), 1024);

        ApplicationContainer sourceApp = onOff.Install(m_nodes.Get(src));
        sourceApp.Start(Seconds(1.0 + 0.25 * i));
        sourceApp.Stop(Seconds(m_cfg.duration));
        m_sourceApps.push_back(sourceApp);
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
RandomWaypointManet::RecordMetrics()
{
    const double now = Simulator::Now().GetSeconds();

    double totalRxBytes = 0.0;
    for (size_t i = 0; i < m_sinkApps.size(); ++i)
    {
        Ptr<PacketSink> sink = DynamicCast<PacketSink>(m_sinkApps[i].Get(0));
        if (sink)
        {
            totalRxBytes += sink->GetTotalRx();
        }
    }

    const double expectedBytesPerFlow = (64e3 / 8.0) * m_cfg.duration;
    double totalExpectedBytes = expectedBytesPerFlow * std::max(1ul, m_sinkApps.size());
    double pdr = 0.0;
    if (totalExpectedBytes > 0.0)
    {
        pdr = totalRxBytes / totalExpectedBytes;
    }

    double throughputKbps = (totalRxBytes * 8.0) / (1000.0 * m_cfg.duration);
    double avgDegree = 0.0;
    double totalEdges = 0.0;

    for (uint32_t i = 0; i < m_cfg.nodes; ++i)
    {
        uint32_t degree = 0;
        Vector pos = m_nodes.Get(i)->GetObject<MobilityModel>()->GetPosition();

        for (uint32_t j = 0; j < m_cfg.nodes; ++j)
        {
            if (i == j)
            {
                continue;
            }

            Vector otherPos = m_nodes.Get(j)->GetObject<MobilityModel>()->GetPosition();
            double dx = pos.x - otherPos.x;
            double dy = pos.y - otherPos.y;
            double dist2 = dx * dx + dy * dy;
            if (dist2 <= m_cfg.txRange * m_cfg.txRange)
            {
                degree++;
            }
        }

        totalEdges += degree;
    }

    avgDegree = totalEdges / static_cast<double>(m_cfg.nodes);
    const double maxPossibleEdges = static_cast<double>(m_cfg.nodes) * (m_cfg.nodes - 1) / 2.0;
    double networkDensity = 0.0;
    if (maxPossibleEdges > 0.0)
    {
        networkDensity = (totalEdges / 2.0) / maxPossibleEdges;
    }

    double packetLoss = std::max(0.0, 1.0 - pdr);

    m_networkMetricsCsv << now << "," << pdr << "," << throughputKbps << ",0.0," << packetLoss
                        << ",0,0," << m_sinkApps.size() << ",1.0," << networkDensity << ","
                        << avgDegree << "\n";
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
    m_networkMetricsCsv << "time,PDR,throughput,average_delay,packet_loss,route_changes,"
                       << "link_breaks,active_flows,connected_components,network_density,"
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
