#ifndef MAINWINDOW_H
#define MAINWINDOW_H

#include "PacketMetrics.h"

#include <QMainWindow>
#include <QMap>
#include <QPointF>
#include <QRectF>

class NodeItem;
class SimulationEngine;
class QChart;
class QChartView;
class QCheckBox;
class QComboBox;
class QDoubleSpinBox;
class QFrame;
class QGraphicsEllipseItem;
class QGraphicsPathItem;
class QGraphicsScene;
class QGridLayout;
class QGraphicsView;
class QLabel;
class QLineSeries;
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
    void onRangeChanged(double metres);
    void onSelectionChanged();

private:
    void buildUi();
    QFrame *buildInfoPanel();
    QWidget *buildMetricsPanel();
    QChartView *makeChart(const QString &title, QLineSeries *series, const QColor &color);
    void buildScene();
    void loadPacketsNextTo(const QString &mobilityCsvPath);
    void setupCharts();
    void updatePackets(double time, const QMap<int, QPointF> &positions);
    void updateMetrics(double time);
    void updateCharts(double time);
    void updateNetwork(double time);
    void updateInfoPanel(double time);
    void fitNetworkInView();
    void setControlsEnabled(bool enabled);
    QFrame *makeStatCard(const QString &caption, QLabel *valueLabel);
    void addInfoRow(QGridLayout *grid, int row, const QString &caption, QLabel *valueLabel);

    // NS-3 metres -> scene coordinates (Y is flipped)
    QPointF toScenePoint(const QPointF &simPosition) const;
    static QString formatTime(double seconds);

    SimulationEngine *m_engine;
    QGraphicsScene *m_scene;
    QGraphicsView *m_view = nullptr;
    QMap<int, NodeItem *> m_nodeItems;
    QMap<int, QGraphicsEllipseItem *> m_rangeItems; // range circle per node
    QGraphicsPathItem *m_linksItem = nullptr;         // all links
    QGraphicsPathItem *m_selectedLinksItem = nullptr; // links of the selected node
    int m_selectedNodeId = -1;                        // -1 = nothing selected
    QGraphicsPathItem *m_packetsItem = nullptr;       // delivered packets in transit
    QGraphicsPathItem *m_lostPacketsItem = nullptr;   // packets that will be lost

    PacketMetrics m_packetMetrics;
    QString m_packetsFileName; // empty when no packets CSV is loaded

    QPushButton *m_playButton = nullptr;
    QPushButton *m_pauseButton = nullptr;
    QPushButton *m_resetButton = nullptr;
    QPushButton *m_openButton = nullptr;
    QComboBox *m_speedCombo = nullptr;
    QLabel *m_fileLabel = nullptr;
    QLabel *m_nodeCountLabel = nullptr;
    QLabel *m_timeLabel = nullptr;
    QLabel *m_linkCountLabel = nullptr;
    QDoubleSpinBox *m_rangeSpinBox = nullptr;
    QCheckBox *m_showAllRangesCheck = nullptr;
    QCheckBox *m_showPacketsCheck = nullptr;

    // Metrics panel
    QLabel *m_pdrLabel = nullptr;
    QLabel *m_throughputLabel = nullptr;
    QLabel *m_delayLabel = nullptr;
    QLabel *m_sentLabel = nullptr;
    QLabel *m_receivedLabel = nullptr;
    QLabel *m_lostLabel = nullptr;
    QLabel *m_packetInfoLabel = nullptr;
    QLineSeries *m_pdrSeries = nullptr;
    QLineSeries *m_throughputSeries = nullptr;
    QLineSeries *m_delaySeries = nullptr;
    QWidget *m_chartsRow = nullptr;
    // Number of points currently shown in each graph (avoids redrawing every frame)
    int m_pdrPointsShown = -1;
    int m_throughputPointsShown = -1;
    int m_delayPointsShown = -1;
    QSlider *m_timelineSlider = nullptr;
    QLabel *m_startTimeLabel = nullptr;
    QLabel *m_endTimeLabel = nullptr;
    QLabel *m_sliderTimeLabel = nullptr;

    // Selected node information panel
    QLabel *m_infoPlaceholder = nullptr;
    QWidget *m_infoDetails = nullptr;
    QLabel *m_infoTitle = nullptr;
    QLabel *m_infoX = nullptr;
    QLabel *m_infoY = nullptr;
    QLabel *m_infoTime = nullptr;
    QLabel *m_infoNeighborCount = nullptr;
    QLabel *m_infoRange = nullptr;
    QLabel *m_infoNeighborList = nullptr;
    QLabel *m_infoPacketsSent = nullptr;
    QLabel *m_infoPacketsReceived = nullptr;

    bool m_dataLoaded = false;
    QRectF m_simBounds; // NS-3 area covered by the CSV
    double m_scale = 1.0; // scene units per metre
};

#endif // MAINWINDOW_H
