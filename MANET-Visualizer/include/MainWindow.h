#ifndef MAINWINDOW_H
#define MAINWINDOW_H

#include <QMainWindow>
#include <QMap>
#include <QPointF>
#include <QRectF>

class NodeItem;
class SimulationEngine;
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
    void buildScene();
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

    bool m_dataLoaded = false;
    QRectF m_simBounds; // NS-3 area covered by the CSV
    double m_scale = 1.0; // scene units per metre
};

#endif // MAINWINDOW_H
