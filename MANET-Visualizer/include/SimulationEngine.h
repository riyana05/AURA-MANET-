#ifndef SIMULATIONENGINE_H
#define SIMULATIONENGINE_H

#include "CsvLoader.h"

#include <QElapsedTimer>
#include <QList>
#include <QMap>
#include <QObject>
#include <QPointF>
#include <QRectF>
#include <QTimer>
#include <QVector>

// A node's position at one CSV timestamp.
struct TimedPosition
{
    double time = 0.0;
    QPointF position; // NS-3 coordinates in metres
};

// Two nodes that are within communication range of each other
struct NodeLink
{
    int nodeA = 0;
    int nodeB = 0;
};

// Owns the simulation clock and answers "where is node N at time T?".
// It knows nothing about widgets or drawing.
class SimulationEngine : public QObject
{
    Q_OBJECT

public:
    explicit SimulationEngine(QObject *parent = nullptr);

    void setSamples(const QVector<MobilitySample> &samples);

    QList<int> nodeIds() const;
    int nodeCount() const;
    double startTime() const;
    double endTime() const;
    double currentTime() const;
    bool isPlaying() const;

    // Smallest rectangle containing every X/Y in the CSV (NS-3 metres)
    QRectF bounds() const;

    // Position of a node at any time, linearly interpolated between the
    // two nearest CSV timestamps.
    QPointF positionAt(int nodeId, double time) const;
    QMap<int, QPointF> positionsAt(double time) const;

    // Two nodes can communicate when distance(A, B) <= communication range
    double communicationRange() const;
    void setCommunicationRange(double metres);
    QVector<NodeLink> linksAt(double time) const;
    QList<int> neighborsAt(int nodeId, double time) const;

public slots:
    void play();
    void pause();
    void reset();
    void setTime(double time);
    void setSpeed(double speed);

signals:
    // Emitted whenever the simulation time (and so node positions) changes
    void timeChanged(double time);
    void playingChanged(bool playing);

private slots:
    void onTick();

private:
    bool isInRange(const QPointF &a, const QPointF &b) const;

    QMap<int, QVector<TimedPosition>> m_tracks; // nodeId -> positions sorted by time
    QRectF m_bounds;
    double m_startTime = 0.0;
    double m_endTime = 0.0;
    double m_currentTime = 0.0;
    double m_speed = 1.0;
    double m_range = 100.0; // metres; the GUI can change it

    QTimer m_timer;
    QElapsedTimer m_clock; // measures real time between timer ticks
};

#endif // SIMULATIONENGINE_H
