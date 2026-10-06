#ifndef PACKETMETRICS_H
#define PACKETMETRICS_H

#include "CsvLoader.h"

#include <QPointF>
#include <QVector>

// Network performance at one moment of the simulation, computed from packets.csv
struct MetricsSnapshot
{
    int sent = 0;     // packets sent so far
    int received = 0; // packets delivered so far
    int lost = 0;     // packets sent so far that never arrive
    int inFlight = 0; // sent, not delivered yet, but will arrive
    double pdrPercent = 0.0;     // received / sent x 100
    double throughputKbps = 0.0; // payload received during the last second
    double averageDelayMs = 0.0; // mean end-to-end delay of packets delivered so far
};

// A packet currently drawn travelling from its source to its destination
struct VisiblePacket
{
    int index = 0;         // index into PacketMetrics::packets()
    double progress = 0.0; // 0 = at the source, 1 = at the destination
};

// Turns the packet list into metrics and graph data.
// Kept separate from SimulationEngine so the engine only deals with time and positions.
class PacketMetrics
{
public:
    void setPackets(const QVector<PacketSample> &packets, double startTime, double endTime);
    void clear();

    bool hasPackets() const;
    const QVector<PacketSample> &packets() const; // sorted by send time

    MetricsSnapshot snapshotAt(double time) const;

    // Packets travelling at 'time'. A delivered packet travels for its real delay,
    // but never less than minTravelTime so very short delays are still visible.
    QVector<VisiblePacket> visiblePacketsAt(double time, double minTravelTime) const;

    // Index of the most recently delivered packet at 'time', or -1
    int lastDeliveredAt(double time) const;

    int sentByNode(int nodeId, double time) const;
    int receivedByNode(int nodeId, double time) const;

    // One point per simulation second, for the graphs
    const QVector<QPointF> &pdrSeries() const;
    const QVector<QPointF> &throughputSeries() const;
    const QVector<QPointF> &delaySeries() const; // mean delay of packets delivered in that second

private:
    static int countUpTo(const QVector<double> &sortedTimes, double time);
    void buildSeries(double startTime, double endTime);

    QVector<PacketSample> m_packets; // sorted by send time
    QVector<double> m_sendTimes;     // m_packets[i].sendTime

    QVector<int> m_receivedOrder;   // indexes of delivered packets, sorted by receive time
    QVector<double> m_receiveTimes; // receive time of m_receivedOrder[i]
    QVector<double> m_bytesPrefix;  // bytes delivered by the first i packets (size n + 1)
    QVector<double> m_delayPrefix;  // summed delay (s) of the first i packets (size n + 1)

    QVector<double> m_lostSendTimes; // send times of lost packets, sorted
    double m_maxDelay = 0.0;

    QVector<QPointF> m_pdrSeries;
    QVector<QPointF> m_throughputSeries;
    QVector<QPointF> m_delaySeries;
};

#endif // PACKETMETRICS_H
