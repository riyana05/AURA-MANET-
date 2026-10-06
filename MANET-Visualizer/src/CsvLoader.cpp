#include "CsvLoader.h"

#include <QFile>
#include <QTextStream>

bool CsvLoader::load(const QString &filePath)
{
    m_samples.clear();
    m_error.clear();
    m_skippedLines = 0;

    QFile file(filePath);
    if (!file.open(QIODevice::ReadOnly | QIODevice::Text)) {
        m_error = QString("Could not open %1:\n%2").arg(filePath, file.errorString());
        return false;
    }

    QTextStream in(&file);
    if (in.atEnd()) {
        m_error = "The CSV file is empty.";
        return false;
    }

    // The header tells us which column holds which value.
    QStringList header = splitCsvLine(in.readLine());
    int timeColumn = findColumn(header, {"time"});
    int nodeColumn = findColumn(header, {"node_id", "nodeid", "node"});
    int xColumn = findColumn(header, {"x"});
    int yColumn = findColumn(header, {"y"});

    if (timeColumn < 0 || nodeColumn < 0 || xColumn < 0 || yColumn < 0) {
        m_error = QString("The CSV header must contain time, node_id, x and y columns.\n"
                          "Found: %1")
                      .arg(header.join(", "));
        return false;
    }

    int columnsNeeded = qMax(qMax(timeColumn, nodeColumn), qMax(xColumn, yColumn)) + 1;

    while (!in.atEnd()) {
        QString line = in.readLine().trimmed();
        if (line.isEmpty()) {
            continue;
        }

        QStringList fields = splitCsvLine(line);
        if (fields.size() < columnsNeeded) {
            m_skippedLines++;
            continue;
        }

        bool timeOk = false;
        bool nodeOk = false;
        bool xOk = false;
        bool yOk = false;

        MobilitySample sample;
        sample.time = fields[timeColumn].toDouble(&timeOk);
        sample.nodeId = fields[nodeColumn].toInt(&nodeOk);
        sample.x = fields[xColumn].toDouble(&xOk);
        sample.y = fields[yColumn].toDouble(&yOk);

        if (!timeOk || !nodeOk || !xOk || !yOk) {
            m_skippedLines++;
            continue;
        }

        m_samples.append(sample);
    }

    if (m_samples.isEmpty()) {
        m_error = "No valid data rows were found in the CSV.";
        return false;
    }

    return true;
}

bool CsvLoader::loadPackets(const QString &filePath)
{
    m_packets.clear();
    m_error.clear();
    m_skippedLines = 0;

    QFile file(filePath);
    if (!file.open(QIODevice::ReadOnly | QIODevice::Text)) {
        m_error = QString("Could not open %1:\n%2").arg(filePath, file.errorString());
        return false;
    }

    QTextStream in(&file);
    if (in.atEnd()) {
        m_error = "The packets CSV file is empty.";
        return false;
    }

    QStringList header = splitCsvLine(in.readLine());
    int sourceColumn = findColumn(header, {"source", "src", "source_node"});
    int destinationColumn = findColumn(header, {"destination", "dst", "destination_node"});
    int sendColumn = findColumn(header, {"send_time", "sendtime", "tx_time"});
    int receiveColumn = findColumn(header, {"receive_time", "receivetime", "rx_time"});
    int statusColumn = findColumn(header, {"status"});
    int idColumn = findColumn(header, {"packet_id", "packetid", "id"});
    int flowColumn = findColumn(header, {"flow_id", "flowid", "flow"});
    int sizeColumn = findColumn(header, {"size_bytes", "size", "bytes"});

    if (sourceColumn < 0 || destinationColumn < 0 || sendColumn < 0
        || (statusColumn < 0 && receiveColumn < 0)) {
        m_error = QString("The packets CSV header must contain source, destination, send_time "
                          "and status or receive_time columns.\nFound: %1")
                      .arg(header.join(", "));
        return false;
    }

    int rowNumber = 0;
    while (!in.atEnd()) {
        QString line = in.readLine().trimmed();
        if (line.isEmpty()) {
            continue;
        }
        rowNumber++;

        // Missing columns at the end of a line are treated as empty
        QStringList fields = splitCsvLine(line);
        while (fields.size() < header.size()) {
            fields.append(QString());
        }

        bool sourceOk = false;
        bool destinationOk = false;
        bool sendOk = false;

        PacketSample packet;
        packet.source = fields[sourceColumn].toInt(&sourceOk);
        packet.destination = fields[destinationColumn].toInt(&destinationOk);
        packet.sendTime = fields[sendColumn].toDouble(&sendOk);
        if (!sourceOk || !destinationOk || !sendOk) {
            m_skippedLines++;
            continue;
        }

        packet.packetId = idColumn >= 0 ? fields[idColumn].toLongLong() : rowNumber;
        packet.flowId = flowColumn >= 0 ? fields[flowColumn].toInt() : -1;
        packet.sizeBytes = sizeColumn >= 0 ? fields[sizeColumn].toInt() : 0;

        bool receiveOk = false;
        double receiveTime = -1.0;
        if (receiveColumn >= 0 && !fields[receiveColumn].isEmpty()) {
            receiveTime = fields[receiveColumn].toDouble(&receiveOk);
        }

        if (statusColumn >= 0) {
            packet.received = fields[statusColumn].toLower() == "received";
        } else {
            packet.received = receiveOk;
        }

        if (packet.received) {
            // A delivered packet must have a valid receive time
            if (!receiveOk || receiveTime < packet.sendTime) {
                m_skippedLines++;
                continue;
            }
            packet.receiveTime = receiveTime;
        }

        m_packets.append(packet);
    }

    if (m_packets.isEmpty()) {
        m_error = "No valid packet rows were found in the CSV.";
        return false;
    }

    return true;
}

const QVector<MobilitySample> &CsvLoader::samples() const
{
    return m_samples;
}

const QVector<PacketSample> &CsvLoader::packets() const
{
    return m_packets;
}

QString CsvLoader::errorString() const
{
    return m_error;
}

int CsvLoader::skippedLines() const
{
    return m_skippedLines;
}

// Splits one CSV line into fields. Commas inside double quotes do not split,
// e.g. the NS-3 neighbors column: 0,2,413.2,121.6,0,2,"14,15"
QStringList CsvLoader::splitCsvLine(const QString &line)
{
    QStringList fields;
    QString current;
    bool insideQuotes = false;

    for (int i = 0; i < line.size(); ++i) {
        QChar c = line[i];

        if (c == QLatin1Char('"')) {
            // Two quotes in a row inside a quoted field mean one literal quote
            if (insideQuotes && i + 1 < line.size() && line[i + 1] == QLatin1Char('"')) {
                current += QLatin1Char('"');
                ++i;
            } else {
                insideQuotes = !insideQuotes;
            }
        } else if (c == QLatin1Char(',') && !insideQuotes) {
            fields.append(current.trimmed());
            current.clear();
        } else {
            current += c;
        }
    }

    fields.append(current.trimmed());
    return fields;
}

// Returns the index of the first header column matching one of the names
// (case-insensitive), or -1 if none match.
int CsvLoader::findColumn(const QStringList &header, const QStringList &acceptedNames)
{
    for (int i = 0; i < header.size(); ++i) {
        QString name = header[i].trimmed().toLower();
        if (acceptedNames.contains(name)) {
            return i;
        }
    }
    return -1;
}
