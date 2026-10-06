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

const QVector<MobilitySample> &CsvLoader::samples() const
{
    return m_samples;
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
