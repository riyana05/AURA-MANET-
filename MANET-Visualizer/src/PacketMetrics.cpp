#include "PacketMetrics.h"

#include <algorithm>
#include <cmath>

static bool sentEarlier(const PacketSample &a, const PacketSample &b)
{
    return a.sendTime < b.sendTime;
}

void PacketMetrics::clear()
{
    m_packets.clear();
    m_sendTimes.clear();
    m_receivedOrder.clear();
    m_receiveTimes.clear();
    m_bytesPrefix.clear();
    m_delayPrefix.clear();
    m_lostSendTimes.clear();
    m_maxDelay = 0.0;
    m_pdrSeries.clear();
    m_throughputSeries.clear();
    m_delaySeries.clear();
}

void PacketMetrics::setPackets(const QVector<PacketSample> &packets, double startTime,
                               double endTime)
{
    clear();
    m_packets = packets;
    std::sort(m_packets.begin(), m_packets.end(), sentEarlier);

    for (int i = 0; i < m_packets.size(); ++i) {
        const PacketSample &packet = m_packets[i];
        m_sendTimes.append(packet.sendTime);

        if (packet.received) {
            m_receivedOrder.append(i);
            m_maxDelay = qMax(m_maxDelay, packet.receiveTime - packet.sendTime);
        } else {
            m_lostSendTimes.append(packet.sendTime);
        }
    }

    // Delivered packets in the order they arrived
    std::sort(m_receivedOrder.begin(), m_receivedOrder.end(), [this](int a, int b) {
        return m_packets[a].receiveTime < m_packets[b].receiveTime;
    });

    // Running totals let any time window be answered with two lookups
    m_bytesPrefix.append(0.0);
    m_delayPrefix.append(0.0);
    for (int index : m_receivedOrder) {
        const PacketSample &packet = m_packets[index];
        m_receiveTimes.append(packet.receiveTime);
        m_bytesPrefix.append(m_bytesPrefix.last() + packet.sizeBytes);
        m_delayPrefix.append(m_delayPrefix.last() + (packet.receiveTime - packet.sendTime));
    }

    buildSeries(startTime, endTime);
}

bool PacketMetrics::hasPackets() const
{
    return !m_packets.isEmpty();
}

const QVector<PacketSample> &PacketMetrics::packets() const
{
    return m_packets;
}

// Number of entries in a sorted list that are <= time
int PacketMetrics::countUpTo(const QVector<double> &sortedTimes, double time)
{
    return int(std::upper_bound(sortedTimes.begin(), sortedTimes.end(), time) - sortedTimes.begin());
}

MetricsSnapshot PacketMetrics::snapshotAt(double time) const
{
    MetricsSnapshot snapshot;
    if (m_packets.isEmpty()) {
        return snapshot;
    }

    snapshot.sent = countUpTo(m_sendTimes, time);
    snapshot.received = countUpTo(m_receiveTimes, time);
    snapshot.lost = countUpTo(m_lostSendTimes, time);
    snapshot.inFlight = snapshot.sent - snapshot.received - snapshot.lost;

    if (snapshot.sent > 0) {
        snapshot.pdrPercent = 100.0 * snapshot.received / snapshot.sent;
    }

    // Payload delivered in the last second (time - 1, time]
    int receivedBefore = countUpTo(m_receiveTimes, time - 1.0);
    double bytes = m_bytesPrefix[snapshot.received] - m_bytesPrefix[receivedBefore];
    snapshot.throughputKbps = bytes * 8.0 / 1000.0;

    if (snapshot.received > 0) {
        snapshot.averageDelayMs = 1000.0 * m_delayPrefix[snapshot.received] / snapshot.received;
    }

    return snapshot;
}

QVector<VisiblePacket> PacketMetrics::visiblePacketsAt(double time, double minTravelTime) const
{
    QVector<VisiblePacket> visible;

    // Only packets sent within the longest possible travel time can still be on their way
    double lookBack = qMax(m_maxDelay, minTravelTime);
    int first = countUpTo(m_sendTimes, time - lookBack);

    for (int i = first; i < m_packets.size() && m_sendTimes[i] <= time; ++i) {
        const PacketSample &packet = m_packets[i];
        double travelTime = minTravelTime;
        if (packet.received) {
            travelTime = qMax(packet.receiveTime - packet.sendTime, minTravelTime);
        }

        double elapsed = time - packet.sendTime;
        if (travelTime > 0.0 && elapsed <= travelTime) {
            VisiblePacket entry;
            entry.index = i;
            entry.progress = elapsed / travelTime;
            visible.append(entry);
        }
    }
    return visible;
}

int PacketMetrics::lastDeliveredAt(double time) const
{
    int received = countUpTo(m_receiveTimes, time);
    return received > 0 ? m_receivedOrder[received - 1] : -1;
}

int PacketMetrics::sentByNode(int nodeId, double time) const
{
    int count = 0;
    int sent = countUpTo(m_sendTimes, time);
    for (int i = 0; i < sent; ++i) {
        if (m_packets[i].source == nodeId) {
            count++;
        }
    }
    return count;
}

int PacketMetrics::receivedByNode(int nodeId, double time) const
{
    int count = 0;
    int received = countUpTo(m_receiveTimes, time);
    for (int i = 0; i < received; ++i) {
        if (m_packets[m_receivedOrder[i]].destination == nodeId) {
            count++;
        }
    }
    return count;
}

const QVector<QPointF> &PacketMetrics::pdrSeries() const
{
    return m_pdrSeries;
}

const QVector<QPointF> &PacketMetrics::throughputSeries() const
{
    return m_throughputSeries;
}

const QVector<QPointF> &PacketMetrics::delaySeries() const
{
    return m_delaySeries;
}

void PacketMetrics::buildSeries(double startTime, double endTime)
{
    for (double second = std::ceil(startTime); second <= endTime; second += 1.0) {
        MetricsSnapshot snapshot = snapshotAt(second);
        m_pdrSeries.append(QPointF(second, snapshot.pdrPercent));
        m_throughputSeries.append(QPointF(second, snapshot.throughputKbps));

        // Mean delay of the packets that arrived during (second - 1, second]
        int before = countUpTo(m_receiveTimes, second - 1.0);
        int upTo = snapshot.received;
        if (upTo > before) {
            double delay = (m_delayPrefix[upTo] - m_delayPrefix[before]) / (upTo - before);
            m_delaySeries.append(QPointF(second, 1000.0 * delay));
        }
    }
}
