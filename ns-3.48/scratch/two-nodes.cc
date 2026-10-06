#include "ns3/core-module.h"
#include "ns3/network-module.h"
#include "ns3/internet-module.h"
#include "ns3/point-to-point-module.h"
#include "ns3/applications-module.h"

using namespace ns3;

int main(int argc, char *argv[])
{
    // 1. CREATE TWO NODES
    NodeContainer nodes;
    nodes.Create(2);
    
    // 2. CREATE POINT-TO-POINT LINK
    PointToPointHelper pointToPoint;
    pointToPoint.SetDeviceAttribute("DataRate", StringValue("5Mbps"));
    pointToPoint.SetChannelAttribute("Delay", StringValue("2ms"));
    
    NetDeviceContainer devices;
    devices = pointToPoint.Install(nodes);
    
    // 3. INSTALL INTERNET STACK
    InternetStackHelper stack;
    stack.Install(nodes);
    
    // 4. ASSIGN IP ADDRESSES
    Ipv4AddressHelper address;
    address.SetBase("10.1.1.0", "255.255.255.0");
    Ipv4InterfaceContainer interfaces = address.Assign(devices);
    
    // 5. CREATE APPLICATION (UDP Echo Server on node 1)
    uint16_t port = 9;
    UdpEchoServerHelper echoServer(port);
    ApplicationContainer serverApps = echoServer.Install(nodes.Get(1));
    serverApps.Start(Seconds(0.0));
    serverApps.Stop(Seconds(10.0));
    
    // 6. CREATE APPLICATION (UDP Echo Client on node 0)
    UdpEchoClientHelper echoClient(interfaces.GetAddress(1), port);
    echoClient.SetAttribute("MaxPackets", UintegerValue(1));
    echoClient.SetAttribute("Interval", TimeValue(Seconds(1.0)));
    echoClient.SetAttribute("PacketSize", UintegerValue(1024));
    
    ApplicationContainer clientApps = echoClient.Install(nodes.Get(0));
    clientApps.Start(Seconds(1.0));
    clientApps.Stop(Seconds(10.0));
    
    // 7. ENABLE PCAP TRACING (optional)
    pointToPoint.EnablePcapAll("two-nodes");
    
    // 8. RUN SIMULATION
    Simulator::Run();
    Simulator::Destroy();
    
    return 0;
}
