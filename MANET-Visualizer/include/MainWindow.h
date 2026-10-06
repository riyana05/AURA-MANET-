#ifndef MAINWINDOW_H
#define MAINWINDOW_H

#include <QMainWindow>
#include <QMap>
#include <QPointF>
#include <QRectF>

class NodeItem;
class SimulationEngine;
class QComboBox;
class QFrame;
class QGraphicsScene;
class QGraphicsView;
class QLabel;
class QPushButton;
class QSlider;

// Builds the GUI and connects the controls to the SimulationEngine.
class MainWindow : public QMainWindow
{
    Q_OBJECT

public:
    explicit MainWindow(QWidget *parent = nullptr);

    bool loadCsv(const QString &filePath);

protected:
    bool eventFilter(QObject *watched, QEvent *event) override;

private slots:
    void openCsvDialog();
    void onTimeChanged(double time);
    void onPlayingChanged(bool playing);
    void onSliderValueChanged(int value);
    void onSpeedChanged(int index);

private:
    void buildUi();
    void buildScene();
    void fitNetworkInView();
    void setControlsEnabled(bool enabled);
    QFrame *makeStatCard(const QString &caption, QLabel *valueLabel);

    // NS-3 metres -> scene coordinates (Y is flipped)
    QPointF toScenePoint(const QPointF &simPosition) const;
    static QString formatTime(double seconds);

    SimulationEngine *m_engine;
    QGraphicsScene *m_scene;
    QGraphicsView *m_view = nullptr;
    QMap<int, NodeItem *> m_nodeItems;

    QPushButton *m_playButton = nullptr;
    QPushButton *m_pauseButton = nullptr;
    QPushButton *m_resetButton = nullptr;
    QPushButton *m_openButton = nullptr;
    QComboBox *m_speedCombo = nullptr;
    QLabel *m_fileLabel = nullptr;
    QLabel *m_nodeCountLabel = nullptr;
    QLabel *m_timeLabel = nullptr;
    QSlider *m_timelineSlider = nullptr;
    QLabel *m_startTimeLabel = nullptr;
    QLabel *m_endTimeLabel = nullptr;
    QLabel *m_sliderTimeLabel = nullptr;

    bool m_dataLoaded = false;
    QRectF m_simBounds; // NS-3 area covered by the CSV
    double m_scale = 1.0; // scene units per metre
};

#endif // MAINWINDOW_H
