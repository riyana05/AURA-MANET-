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

// Reads and parses the NS-3 node mobility CSV. It does nothing else.
//
// Columns are found by their header name, so their order does not matter.
// Required: time, node_id (or nodeId / node), x, y. Other columns are ignored.
class CsvLoader
{
public:
    bool load(const QString &filePath);

    const QVector<MobilitySample> &samples() const;
    QString errorString() const;
    int skippedLines() const;

private:
    static QStringList splitCsvLine(const QString &line);
    static int findColumn(const QStringList &header, const QStringList &acceptedNames);

    QVector<MobilitySample> m_samples;
    QString m_error;
    int m_skippedLines = 0;
};

#endif // CSVLOADER_H
