#ifndef CSVLOADER_H
#define CSVLOADER_H

#include <QString>
#include <QStringList>
#include <QVector>

// One row of the NS-3 mobility CSV: where one node was at one moment.
struct MobilitySample
{
    double time = 0.0; // simulation time in seconds
    int nodeId = 0;
    double x = 0.0; // NS-3 X coordinate in metres
    double y = 0.0; // NS-3 Y coordinate in metres
};

// One application packet from the NS-3 packets CSV.
struct PacketSample
{
    qint64 packetId = 0;
    int flowId = -1;
    int source = 0;      // node that sent the packet
    int destination = 0; // node the packet was addressed to
    double sendTime = 0.0;     // seconds
    double receiveTime = -1.0; // seconds, -1 if the packet was lost
    int sizeBytes = 0;
    bool received = false;
};

// Reads and parses the NS-3 CSV files. It does nothing else.
//
// Columns are found by their header name, so their order does not matter.
// Mobility CSV - required: time, node_id (or nodeId / node), x, y.
// Packets CSV  - required: source, destination, send_time, and status or receive_time.
//                optional: packet_id, flow_id, size_bytes.
// Other columns are ignored.
class CsvLoader
{
public:
    bool load(const QString &filePath); // mobility CSV
    bool loadPackets(const QString &filePath);

    const QVector<MobilitySample> &samples() const;
    const QVector<PacketSample> &packets() const;
    QString errorString() const;
    int skippedLines() const;

private:
    static QStringList splitCsvLine(const QString &line);
    static int findColumn(const QStringList &header, const QStringList &acceptedNames);

    QVector<MobilitySample> m_samples;
    QVector<PacketSample> m_packets;
    QString m_error;
    int m_skippedLines = 0;
};

#endif // CSVLOADER_H
