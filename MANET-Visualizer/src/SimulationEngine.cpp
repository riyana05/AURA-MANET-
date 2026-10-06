#include "SimulationEngine.h"

#include <algorithm>

// About 60 updates per second
static const int TickIntervalMs = 16;

static bool isEarlier(const TimedPosition &a, const TimedPosition &b)
{
    return a.time < b.time;
}

SimulationEngine::SimulationEngine(QObject *parent)
    : QObject(parent)
{
    m_timer.setInterval(TickIntervalMs);
    connect(&m_timer, &QTimer::timeout, this, &SimulationEngine::onTick);
}

void SimulationEngine::setSamples(const QVector<MobilitySample> &samples)
{
    pause();
    m_tracks.clear();

    if (samples.isEmpty()) {
        m_bounds = QRectF();
        m_startTime = m_endTime = m_currentTime = 0.0;
        emit timeChanged(m_currentTime);
        return;
    }

    double minX = samples.first().x;
    double maxX = minX;
    double minY = samples.first().y;
    double maxY = minY;
    m_startTime = samples.first().time;
    m_endTime = m_startTime;

    for (const MobilitySample &sample : samples) {
        TimedPosition entry;
        entry.time = sample.time;
        entry.position = QPointF(sample.x, sample.y);
        m_tracks[sample.nodeId].append(entry);

        minX = qMin(minX, sample.x);
        maxX = qMax(maxX, sample.x);
        minY = qMin(minY, sample.y);
        maxY = qMax(maxY, sample.y);
        m_startTime = qMin(m_startTime, sample.time);
        m_endTime = qMax(m_endTime, sample.time);
    }

    // Do not rely on the CSV being sorted
    for (QVector<TimedPosition> &track : m_tracks) {
        std::sort(track.begin(), track.end(), isEarlier);
    }

    m_bounds = QRectF(QPointF(minX, minY), QPointF(maxX, maxY));
    m_currentTime = m_startTime;
    emit timeChanged(m_currentTime);
}

QList<int> SimulationEngine::nodeIds() const
{
    return m_tracks.keys();
}

int SimulationEngine::nodeCount() const
{
    return m_tracks.size();
}

double SimulationEngine::startTime() const
{
    return m_startTime;
}

double SimulationEngine::endTime() const
{
    return m_endTime;
}

double SimulationEngine::currentTime() const
{
    return m_currentTime;
}

bool SimulationEngine::isPlaying() const
{
    return m_timer.isActive();
}

QRectF SimulationEngine::bounds() const
{
    return m_bounds;
}

QPointF SimulationEngine::positionAt(int nodeId, double time) const
{
    auto found = m_tracks.constFind(nodeId);
    if (found == m_tracks.constEnd() || found.value().isEmpty()) {
        return QPointF();
    }

    const QVector<TimedPosition> &track = found.value();

    // Before the first or after the last sample: stay at that position
    if (time <= track.first().time) {
        return track.first().position;
    }
    if (time >= track.last().time) {
        return track.last().position;
    }

    // Binary search for the two samples around 'time':
    // track[low].time <= time < track[high].time
    int low = 0;
    int high = track.size() - 1;
    while (high - low > 1) {
        int middle = (low + high) / 2;
        if (track[middle].time <= time) {
            low = middle;
        } else {
            high = middle;
        }
    }

    const TimedPosition &before = track[low];
    const TimedPosition &after = track[high];
    double span = after.time - before.time;
    if (span <= 0.0) {
        return after.position;
    }

    double fraction = (time - before.time) / span;
    return before.position + (after.position - before.position) * fraction;
}

void SimulationEngine::play()
{
    if (m_tracks.isEmpty() || m_timer.isActive()) {
        return;
    }

    // Pressing Play at the end starts again from the beginning
    if (m_currentTime >= m_endTime) {
        setTime(m_startTime);
    }

    m_clock.start();
    m_timer.start();
    emit playingChanged(true);
}

void SimulationEngine::pause()
{
    if (!m_timer.isActive()) {
        return;
    }

    m_timer.stop();
    emit playingChanged(false);
}

void SimulationEngine::reset()
{
    pause();
    setTime(m_startTime);
}

void SimulationEngine::setTime(double time)
{
    m_currentTime = qBound(m_startTime, time, m_endTime);
    emit timeChanged(m_currentTime);
}

void SimulationEngine::setSpeed(double speed)
{
    if (speed > 0.0) {
        m_speed = speed;
    }
}

void SimulationEngine::onTick()
{
    // Advance by the real time that passed, so playback speed does not
    // depend on how regularly the timer fires.
    double realSeconds = m_clock.restart() / 1000.0;
    double nextTime = m_currentTime + realSeconds * m_speed;

    if (nextTime >= m_endTime) {
        setTime(m_endTime);
        pause();
    } else {
        setTime(nextTime);
    }
}
