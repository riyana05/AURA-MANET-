#include "MainWindow.h"

#include "CsvLoader.h"
#include "NodeItem.h"
#include "SimulationEngine.h"

#include <QChart>
#include <QChartView>
#include <QCheckBox>
#include <QComboBox>
#include <QDir>
#include <QDoubleSpinBox>
#include <QEvent>
#include <QFileDialog>
#include <QFileInfo>
#include <QFrame>
#include <QGraphicsEllipseItem>
#include <QGraphicsPathItem>
#include <QGraphicsRectItem>
#include <QGraphicsScene>
#include <QGraphicsSimpleTextItem>
#include <QGraphicsView>
#include <QGridLayout>
#include <QHBoxLayout>
#include <QLabel>
#include <QLineSeries>
#include <QMessageBox>
#include <QPainterPath>
#include <QPushButton>
#include <QSignalBlocker>
#include <QSlider>
#include <QSplitter>
#include <QStatusBar>
#include <QVBoxLayout>
#include <QValueAxis>

#include <cmath>

// The longer side of the network area is drawn this many scene units long.
// The view then scales the whole scene to fit the window.
static const double NetworkDrawSize = 900.0;
// Empty space around the network area (room for the axis labels)
static const double ScenePadding = 70.0;
static const double NodeRadius = 14.0;
static const double PacketDotRadius = 5.0;
// Most packets arrive within a few milliseconds, far too fast to see, so every
// packet dot stays on screen for at least this long in real (wall-clock) seconds.
static const double PacketMinVisibleSeconds = 0.4;

static const char *StyleSheet = R"(
QWidget#central { background: #0b1220; }

QFrame#header { background: #0f172a; border-bottom: 1px solid #1e293b; }
QLabel#title { color: #e2e8f0; font-size: 18px; font-weight: 700; }
QLabel#fileLabel { color: #94a3b8; font-size: 12px; }

QFrame#sidePanel { background: #0f172a; border-right: 1px solid #1e293b; }
QLabel#sectionHeading { color: #64748b; font-size: 11px; font-weight: 700; }
QLabel#caption { color: #94a3b8; font-size: 12px; }

QFrame#statCard { background: #111c33; border: 1px solid #1e293b; border-radius: 10px; }
QLabel#statValue { color: #f1f5f9; font-size: 22px; font-weight: 600; }

QPushButton {
    background: #1e293b; color: #e2e8f0;
    border: 1px solid #334155; border-radius: 8px;
    padding: 9px 14px; font-size: 13px; text-align: left;
}
QPushButton:hover { background: #273449; border-color: #475569; }
QPushButton:pressed { background: #334155; }
QPushButton:disabled { color: #475569; background: #131c2e; border-color: #1e293b; }
QPushButton#playButton { background: #0ea5e9; color: #04111f; border: none; font-weight: 600; }
QPushButton#playButton:hover { background: #38bdf8; }
QPushButton#playButton:disabled { background: #13324a; color: #3b5b75; }
QPushButton#toggleButton:checked { background: #14532d; border-color: #22c55e; color: #bbf7d0; }
QPushButton#toggleButton:checked:hover { background: #166534; }

QComboBox {
    background: #1e293b; color: #e2e8f0;
    border: 1px solid #334155; border-radius: 8px; padding: 6px 10px;
}
QComboBox:disabled { color: #475569; }
QComboBox QAbstractItemView {
    background: #1e293b; color: #e2e8f0; selection-background-color: #0ea5e9;
}

QDoubleSpinBox {
    background: #1e293b; color: #e2e8f0;
    border: 1px solid #334155; border-radius: 8px; padding: 6px 10px;
}
QDoubleSpinBox:disabled { color: #475569; }
QCheckBox { color: #cbd5e1; font-size: 12px; spacing: 8px; }
QCheckBox:disabled { color: #475569; }

QFrame#infoPanel { background: #0f172a; border-left: 1px solid #1e293b; }
QLabel#infoTitle { color: #f1f5f9; font-size: 20px; font-weight: 600; }
QLabel#infoValue { color: #f1f5f9; font-size: 14px; font-weight: 600; }
QLabel#placeholder { color: #64748b; font-size: 13px; }

QFrame#metricsPanel { background: #0b1220; border-top: 1px solid #1e293b; }
QLabel#metricValue { color: #f1f5f9; font-size: 18px; font-weight: 600; }
QLabel#packetInfo { color: #cbd5e1; font-size: 12px; }
QLabel#legend { color: #64748b; font-size: 11px; }
QSplitter::handle { background: #1e293b; }
QSplitter::handle:vertical { height: 3px; }

QFrame#timelinePanel { background: #0f172a; border-top: 1px solid #1e293b; }
QLabel#timelineEdge { color: #64748b; font-size: 12px; }
QLabel#timelineCurrent { color: #38bdf8; font-size: 13px; font-weight: 600; }

QSlider::groove:horizontal { height: 6px; background: #1e293b; border-radius: 3px; }
QSlider::sub-page:horizontal { background: #0ea5e9; border-radius: 3px; }
QSlider::handle:horizontal {
    background: #e2e8f0; width: 16px; height: 16px; margin: -5px 0; border-radius: 8px;
}
QSlider::handle:horizontal:disabled { background: #334155; }

QStatusBar { background: #0f172a; color: #64748b; }
QGraphicsView#networkView { background: #0b1220; border: none; }
)";

// Picks a round grid spacing (1, 2 or 5 x 10^n) giving about 8 lines
static double niceGridStep(double range)
{
    if (range <= 0.0) {
        return 1.0;
    }

    double rough = range / 8.0;
    double magnitude = std::pow(10.0, std::floor(std::log10(rough)));
    double fraction = rough / magnitude;

    double nice = 10.0;
    if (fraction < 1.5) {
        nice = 1.0;
    } else if (fraction < 3.5) {
        nice = 2.0;
    } else if (fraction < 7.5) {
        nice = 5.0;
    }
    return nice * magnitude;
}

// A different, evenly spread colour for every node ID
static QColor nodeColor(int nodeId)
{
    double hue = std::fmod(nodeId * 0.618033988749895, 1.0);
    return QColor::fromHsvF(hue, 0.55, 0.98);
}

MainWindow::MainWindow(QWidget *parent)
    : QMainWindow(parent)
    , m_engine(new SimulationEngine(this))
    , m_scene(new QGraphicsScene(this))
{
    buildUi();

    connect(m_engine, &SimulationEngine::timeChanged, this, &MainWindow::onTimeChanged);
    connect(m_engine, &SimulationEngine::playingChanged, this, &MainWindow::onPlayingChanged);

    connect(m_playButton, &QPushButton::clicked, m_engine, &SimulationEngine::play);
    connect(m_pauseButton, &QPushButton::clicked, m_engine, &SimulationEngine::pause);
    connect(m_resetButton, &QPushButton::clicked, m_engine, &SimulationEngine::reset);
    connect(m_openButton, &QPushButton::clicked, this, &MainWindow::openCsvDialog);
    connect(m_timelineSlider, &QSlider::valueChanged, this, &MainWindow::onSliderValueChanged);
    connect(m_speedCombo, &QComboBox::currentIndexChanged, this, &MainWindow::onSpeedChanged);
    connect(m_rangeSpinBox, &QDoubleSpinBox::valueChanged, this, &MainWindow::onRangeChanged);
    connect(m_showAllRangesCheck, &QCheckBox::toggled, this, [this]() {
        updateNetwork(m_engine->currentTime());
    });
    connect(m_packetsToggle, &QPushButton::toggled, this, &MainWindow::onPacketsToggled);
    connect(m_scene, &QGraphicsScene::selectionChanged, this, &MainWindow::onSelectionChanged);

    // Refit the network whenever the view changes size
    m_view->viewport()->installEventFilter(this);

    setControlsEnabled(false);
}

void MainWindow::buildUi()
{
    setWindowTitle("MANET Visualizer");
    setStyleSheet(StyleSheet);

    QWidget *central = new QWidget;
    central->setObjectName("central");
    QVBoxLayout *rootLayout = new QVBoxLayout(central);
    rootLayout->setContentsMargins(0, 0, 0, 0);
    rootLayout->setSpacing(0);

    // ---- Header ----
    QFrame *header = new QFrame;
    header->setObjectName("header");
    QHBoxLayout *headerLayout = new QHBoxLayout(header);
    headerLayout->setContentsMargins(20, 14, 20, 14);

    QLabel *title = new QLabel("MANET VISUALIZER");
    title->setObjectName("title");
    QFont titleFont = title->font();
    titleFont.setLetterSpacing(QFont::AbsoluteSpacing, 3);
    title->setFont(titleFont);

    m_fileLabel = new QLabel("No CSV loaded");
    m_fileLabel->setObjectName("fileLabel");

    headerLayout->addWidget(title);
    headerLayout->addStretch();
    headerLayout->addWidget(m_fileLabel);

    // ---- Left panel ----
    QFrame *sidePanel = new QFrame;
    sidePanel->setObjectName("sidePanel");
    sidePanel->setFixedWidth(240);
    QVBoxLayout *sideLayout = new QVBoxLayout(sidePanel);
    sideLayout->setContentsMargins(18, 20, 18, 18);
    sideLayout->setSpacing(10);

    QLabel *simulationHeading = new QLabel("SIMULATION");
    simulationHeading->setObjectName("sectionHeading");

    m_playButton = new QPushButton("▶   Play");
    m_playButton->setObjectName("playButton");
    m_pauseButton = new QPushButton("❚❚   Pause");
    m_resetButton = new QPushButton("↻   Reset");

    QLabel *speedCaption = new QLabel("Speed");
    speedCaption->setObjectName("caption");
    m_speedCombo = new QComboBox;
    const double speeds[] = {0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0};
    for (double speed : speeds) {
        m_speedCombo->addItem(QString::number(speed) + "x", speed);
    }
    m_speedCombo->setCurrentIndex(m_speedCombo->findData(1.0));

    QLabel *networkHeading = new QLabel("NETWORK");
    networkHeading->setObjectName("sectionHeading");

    QLabel *rangeCaption = new QLabel("Communication range");
    rangeCaption->setObjectName("caption");
    m_rangeSpinBox = new QDoubleSpinBox;
    m_rangeSpinBox->setRange(1.0, 100000.0);
    m_rangeSpinBox->setDecimals(1);
    m_rangeSpinBox->setSingleStep(10.0);
    m_rangeSpinBox->setSuffix(" m");
    m_rangeSpinBox->setValue(m_engine->communicationRange());

    m_showAllRangesCheck = new QCheckBox("Show range of every node");
    // Switch that turns the packet animation on and off (shortcut: P)
    m_packetsToggle = new QPushButton;
    m_packetsToggle->setObjectName("toggleButton");
    m_packetsToggle->setCheckable(true);
    m_packetsToggle->setChecked(true);
    m_packetsToggle->setShortcut(QKeySequence(Qt::Key_P));
    m_packetsToggle->setToolTip("Show or hide the moving packets (P)");
    onPacketsToggled(true);

    m_nodeCountLabel = new QLabel("–");
    m_linkCountLabel = new QLabel("–");
    m_timeLabel = new QLabel("–");

    QHBoxLayout *countsRow = new QHBoxLayout;
    countsRow->setSpacing(10);
    countsRow->addWidget(makeStatCard("Nodes", m_nodeCountLabel));
    countsRow->addWidget(makeStatCard("Links", m_linkCountLabel));

    m_openButton = new QPushButton("Open CSV…");

    sideLayout->addWidget(simulationHeading);
    sideLayout->addSpacing(4);
    sideLayout->addWidget(m_playButton);
    sideLayout->addWidget(m_pauseButton);
    sideLayout->addWidget(m_resetButton);
    sideLayout->addSpacing(8);
    sideLayout->addWidget(speedCaption);
    sideLayout->addWidget(m_speedCombo);
    sideLayout->addSpacing(14);
    sideLayout->addWidget(networkHeading);
    sideLayout->addSpacing(4);
    sideLayout->addWidget(rangeCaption);
    sideLayout->addWidget(m_rangeSpinBox);
    sideLayout->addWidget(m_showAllRangesCheck);
    sideLayout->addSpacing(4);
    sideLayout->addWidget(m_packetsToggle);
    sideLayout->addSpacing(14);
    sideLayout->addLayout(countsRow);
    sideLayout->addWidget(makeStatCard("Simulation time", m_timeLabel));
    sideLayout->addStretch();
    sideLayout->addWidget(m_openButton);

    // ---- Network area ----
    m_view = new QGraphicsView(m_scene);
    m_view->setObjectName("networkView");
    m_view->setRenderHint(QPainter::Antialiasing);
    m_view->setHorizontalScrollBarPolicy(Qt::ScrollBarAlwaysOff);
    m_view->setVerticalScrollBarPolicy(Qt::ScrollBarAlwaysOff);
    m_view->setFrameShape(QFrame::NoFrame);
    m_view->setBackgroundBrush(QColor("#0b1220"));

    QHBoxLayout *middleLayout = new QHBoxLayout;
    middleLayout->setContentsMargins(0, 0, 0, 0);
    middleLayout->setSpacing(0);
    middleLayout->addWidget(sidePanel);

    // Network on top, metrics and graphs below; the user can drag the divider
    QSplitter *centerSplitter = new QSplitter(Qt::Vertical);
    centerSplitter->addWidget(m_view);
    centerSplitter->addWidget(buildMetricsPanel());
    centerSplitter->setStretchFactor(0, 1);
    centerSplitter->setStretchFactor(1, 0);
    centerSplitter->setChildrenCollapsible(false);
    centerSplitter->setSizes({640, 360}); // initial split, in proportion

    middleLayout->addWidget(centerSplitter, 1);
    middleLayout->addWidget(buildInfoPanel());

    // ---- Timeline ----
    QFrame *timelinePanel = new QFrame;
    timelinePanel->setObjectName("timelinePanel");
    QVBoxLayout *timelineLayout = new QVBoxLayout(timelinePanel);
    timelineLayout->setContentsMargins(20, 12, 20, 12);
    timelineLayout->setSpacing(4);

    m_startTimeLabel = new QLabel("0.00 s");
    m_startTimeLabel->setObjectName("timelineEdge");
    m_endTimeLabel = new QLabel("0.00 s");
    m_endTimeLabel->setObjectName("timelineEdge");
    m_timelineSlider = new QSlider(Qt::Horizontal);
    m_sliderTimeLabel = new QLabel("0.00 s");
    m_sliderTimeLabel->setObjectName("timelineCurrent");
    m_sliderTimeLabel->setAlignment(Qt::AlignCenter);

    QHBoxLayout *sliderRow = new QHBoxLayout;
    sliderRow->setSpacing(12);
    sliderRow->addWidget(m_startTimeLabel);
    sliderRow->addWidget(m_timelineSlider, 1);
    sliderRow->addWidget(m_endTimeLabel);

    timelineLayout->addLayout(sliderRow);
    timelineLayout->addWidget(m_sliderTimeLabel);

    rootLayout->addWidget(header);
    rootLayout->addLayout(middleLayout, 1);
    rootLayout->addWidget(timelinePanel);

    setCentralWidget(central);
    statusBar()->showMessage("Open an NS-3 mobility CSV to begin.");
}

QFrame *MainWindow::makeStatCard(const QString &caption, QLabel *valueLabel)
{
    QFrame *card = new QFrame;
    card->setObjectName("statCard");
    QVBoxLayout *layout = new QVBoxLayout(card);
    layout->setContentsMargins(14, 10, 14, 10);
    layout->setSpacing(2);

    QLabel *captionLabel = new QLabel(caption);
    captionLabel->setObjectName("caption");
    valueLabel->setObjectName("statValue");

    layout->addWidget(captionLabel);
    layout->addWidget(valueLabel);
    return card;
}

QFrame *MainWindow::buildInfoPanel()
{
    QFrame *panel = new QFrame;
    panel->setObjectName("infoPanel");
    panel->setFixedWidth(250);
    QVBoxLayout *layout = new QVBoxLayout(panel);
    layout->setContentsMargins(18, 20, 18, 18);
    layout->setSpacing(10);

    QLabel *heading = new QLabel("SELECTED NODE");
    heading->setObjectName("sectionHeading");

    m_infoPlaceholder = new QLabel("No node selected\n\nClick a node in the network to see its details.");
    m_infoPlaceholder->setObjectName("placeholder");
    m_infoPlaceholder->setWordWrap(true);

    // Shown instead of the placeholder when a node is selected
    m_infoDetails = new QWidget;
    QVBoxLayout *detailsLayout = new QVBoxLayout(m_infoDetails);
    detailsLayout->setContentsMargins(0, 0, 0, 0);
    detailsLayout->setSpacing(10);

    m_infoTitle = new QLabel;
    m_infoTitle->setObjectName("infoTitle");

    QFrame *card = new QFrame;
    card->setObjectName("statCard");
    QGridLayout *grid = new QGridLayout(card);
    grid->setContentsMargins(14, 12, 14, 12);
    grid->setVerticalSpacing(10);

    m_infoX = new QLabel;
    m_infoY = new QLabel;
    m_infoTime = new QLabel;
    m_infoNeighborCount = new QLabel;
    m_infoRange = new QLabel;
    addInfoRow(grid, 0, "X", m_infoX);
    addInfoRow(grid, 1, "Y", m_infoY);
    addInfoRow(grid, 2, "Simulation time", m_infoTime);
    addInfoRow(grid, 3, "Neighbors", m_infoNeighborCount);
    addInfoRow(grid, 4, "Range", m_infoRange);

    m_infoPacketsSent = new QLabel;
    m_infoPacketsReceived = new QLabel;
    addInfoRow(grid, 5, "Packets sent", m_infoPacketsSent);
    addInfoRow(grid, 6, "Packets received", m_infoPacketsReceived);

    QLabel *neighborListCaption = new QLabel("Neighbor IDs");
    neighborListCaption->setObjectName("caption");
    m_infoNeighborList = new QLabel;
    m_infoNeighborList->setObjectName("infoValue");
    m_infoNeighborList->setWordWrap(true);

    detailsLayout->addWidget(m_infoTitle);
    detailsLayout->addWidget(card);
    detailsLayout->addSpacing(4);
    detailsLayout->addWidget(neighborListCaption);
    detailsLayout->addWidget(m_infoNeighborList);

    layout->addWidget(heading);
    layout->addSpacing(4);
    layout->addWidget(m_infoPlaceholder);
    layout->addWidget(m_infoDetails);
    layout->addStretch();

    m_infoDetails->hide();
    return panel;
}

void MainWindow::addInfoRow(QGridLayout *grid, int row, const QString &caption, QLabel *valueLabel)
{
    QLabel *captionLabel = new QLabel(caption);
    captionLabel->setObjectName("caption");
    valueLabel->setObjectName("infoValue");
    valueLabel->setAlignment(Qt::AlignRight | Qt::AlignVCenter);

    grid->addWidget(captionLabel, row, 0);
    grid->addWidget(valueLabel, row, 1);
}

QWidget *MainWindow::buildMetricsPanel()
{
    QFrame *panel = new QFrame;
    panel->setObjectName("metricsPanel");
    QVBoxLayout *layout = new QVBoxLayout(panel);
    layout->setContentsMargins(16, 12, 16, 12);
    layout->setSpacing(8);

    QLabel *heading = new QLabel("NETWORK PERFORMANCE");
    heading->setObjectName("sectionHeading");

    m_pdrLabel = new QLabel("–");
    m_throughputLabel = new QLabel("–");
    m_delayLabel = new QLabel("–");
    m_sentLabel = new QLabel("–");
    m_receivedLabel = new QLabel("–");
    m_lostLabel = new QLabel("–");

    QHBoxLayout *cardsRow = new QHBoxLayout;
    cardsRow->setSpacing(10);
    cardsRow->addWidget(makeStatCard("PDR", m_pdrLabel));
    cardsRow->addWidget(makeStatCard("Throughput (last 1 s)", m_throughputLabel));
    cardsRow->addWidget(makeStatCard("Average delay", m_delayLabel));
    cardsRow->addWidget(makeStatCard("Sent", m_sentLabel));
    cardsRow->addWidget(makeStatCard("Received", m_receivedLabel));
    cardsRow->addWidget(makeStatCard("Lost", m_lostLabel));
    const QList<QLabel *> metricLabels = {m_pdrLabel, m_throughputLabel, m_delayLabel,
                                          m_sentLabel, m_receivedLabel, m_lostLabel};
    for (QLabel *label : metricLabels) {
        label->setObjectName("metricValue");
    }

    m_packetInfoLabel = new QLabel("No packets CSV loaded.");
    m_packetInfoLabel->setObjectName("packetInfo");
    m_packetInfoLabel->setWordWrap(true);

    QLabel *legend = new QLabel(
        "<span style='color:#4ade80'>●</span> delivered packet &nbsp;&nbsp;"
        "<span style='color:#f87171'>●</span> lost packet &nbsp;&nbsp;— "
        "packets are drawn on a straight line from source to destination "
        "(the hops in between are not recorded) and stay visible for at least "
        + QString::number(PacketMinVisibleSeconds) + " s.");
    legend->setObjectName("legend");
    legend->setWordWrap(true);

    m_pdrSeries = new QLineSeries;
    m_throughputSeries = new QLineSeries;
    m_delaySeries = new QLineSeries;

    m_chartsRow = new QWidget;
    QHBoxLayout *chartsLayout = new QHBoxLayout(m_chartsRow);
    chartsLayout->setContentsMargins(0, 0, 0, 0);
    chartsLayout->setSpacing(10);
    chartsLayout->addWidget(makeChart("PDR (%)", m_pdrSeries, QColor("#4ade80")));
    chartsLayout->addWidget(makeChart("Throughput (kbps)", m_throughputSeries, QColor("#38bdf8")));
    chartsLayout->addWidget(makeChart("Delay (ms, per second)", m_delaySeries, QColor("#fbbf24")));

    layout->addWidget(heading);
    layout->addLayout(cardsRow);
    layout->addWidget(m_packetInfoLabel);
    layout->addWidget(legend);
    layout->addWidget(m_chartsRow, 1);
    return panel;
}

QChartView *MainWindow::makeChart(const QString &title, QLineSeries *series, const QColor &color)
{
    QChart *chart = new QChart;
    chart->legend()->hide();
    chart->setAnimationOptions(QChart::NoAnimation);
    chart->setBackgroundBrush(QColor("#111c33"));
    chart->setBackgroundPen(QPen(QColor("#1e293b")));
    chart->setBackgroundRoundness(10);
    chart->setMargins(QMargins(4, 0, 8, 2));

    QFont titleFont;
    titleFont.setPixelSize(12);
    titleFont.setBold(true);
    chart->setTitle(title);
    chart->setTitleFont(titleFont);
    chart->setTitleBrush(QColor("#94a3b8"));

    series->setPen(QPen(color, 2));
    chart->addSeries(series);

    QFont axisFont;
    axisFont.setPixelSize(10);

    QValueAxis *axisX = new QValueAxis;
    QValueAxis *axisY = new QValueAxis;
    const QList<QValueAxis *> axes = {axisX, axisY};
    for (QValueAxis *axis : axes) {
        axis->setLabelFormat("%.0f");
        axis->setLabelsFont(axisFont);
        axis->setLabelsColor(QColor("#64748b"));
        axis->setGridLineColor(QColor("#1e293b"));
        axis->setLinePenColor(QColor("#334155"));
        axis->setTickCount(5);
    }
    chart->addAxis(axisX, Qt::AlignBottom);
    chart->addAxis(axisY, Qt::AlignLeft);
    series->attachAxis(axisX);
    series->attachAxis(axisY);

    QChartView *view = new QChartView(chart);
    view->setRenderHint(QPainter::Antialiasing);
    view->setMinimumHeight(140);
    view->setMaximumHeight(240);
    view->setStyleSheet("background: transparent;");
    return view;
}

bool MainWindow::loadCsv(const QString &filePath)
{
    CsvLoader loader;
    if (!loader.load(filePath)) {
        QMessageBox::warning(this, "Could not load CSV", loader.errorString());
        return false;
    }

    // Remove the previous simulation's items before the engine announces the new data.
    // Signals are blocked so onSelectionChanged does not run while items are deleted.
    {
        QSignalBlocker blocker(m_scene);
        m_scene->clear();
    }
    m_nodeItems.clear();
    m_rangeItems.clear();
    m_linksItem = nullptr;
    m_selectedLinksItem = nullptr;
    m_packetsItem = nullptr;
    m_lostPacketsItem = nullptr;
    m_selectedNodeId = -1;

    m_engine->setSamples(loader.samples());
    buildScene();
    loadPacketsNextTo(filePath);
    setupCharts();

    double duration = m_engine->endTime() - m_engine->startTime();
    {
        QSignalBlocker blocker(m_timelineSlider);
        // The slider works in milliseconds from the first timestamp
        m_timelineSlider->setRange(0, qRound(duration * 1000.0));
    }
    m_startTimeLabel->setText(formatTime(m_engine->startTime()));
    m_endTimeLabel->setText(formatTime(m_engine->endTime()));
    m_nodeCountLabel->setText(QString::number(m_engine->nodeCount()));
    QString fileNames = QFileInfo(filePath).fileName();
    if (!m_packetsFileName.isEmpty()) {
        fileNames += "  +  " + m_packetsFileName;
    }
    m_fileLabel->setText(fileNames);

    m_dataLoaded = true;
    setControlsEnabled(true);
    onTimeChanged(m_engine->currentTime());
    fitNetworkInView();

    QString message = QString("Loaded %1 rows from %2")
                          .arg(loader.samples().size())
                          .arg(QFileInfo(filePath).fileName());
    if (loader.skippedLines() > 0) {
        message += QString(" (%1 invalid lines skipped)").arg(loader.skippedLines());
    }
    if (m_packetMetrics.hasPackets()) {
        message += QString(", %1 packets from %2")
                       .arg(m_packetMetrics.packets().size())
                       .arg(m_packetsFileName);
    } else {
        message += " (no packets.csv found next to it)";
    }
    statusBar()->showMessage(message);
    return true;
}

void MainWindow::loadPacketsNextTo(const QString &mobilityCsvPath)
{
    m_packetMetrics.clear();
    m_packetsFileName.clear();

    QString packetsPath = QFileInfo(mobilityCsvPath).dir().filePath("packets.csv");
    if (!QFile::exists(packetsPath)) {
        return;
    }

    CsvLoader loader;
    if (!loader.loadPackets(packetsPath)) {
        QMessageBox::warning(this, "Could not load packets CSV", loader.errorString());
        return;
    }

    m_packetMetrics.setPackets(loader.packets(), m_engine->startTime(), m_engine->endTime());
    m_packetsFileName = QFileInfo(packetsPath).fileName();
}

// Highest Y value in a series, used to scale the graph's Y axis
static double maxY(const QVector<QPointF> &points)
{
    double highest = 0.0;
    for (const QPointF &point : points) {
        highest = qMax(highest, point.y());
    }
    return highest;
}

static void setAxisRanges(QLineSeries *series, double minX, double maxX, double maxYValue)
{
    const QList<QAbstractAxis *> axes = series->attachedAxes();
    for (QAbstractAxis *axis : axes) {
        QValueAxis *valueAxis = qobject_cast<QValueAxis *>(axis);
        if (!valueAxis) {
            continue;
        }
        if (valueAxis->orientation() == Qt::Horizontal) {
            valueAxis->setRange(minX, maxX);
        } else {
            valueAxis->setRange(0.0, maxYValue > 0.0 ? maxYValue : 1.0);
            valueAxis->applyNiceNumbers(); // round tick values, e.g. 0, 200, 400 ...
        }
    }
}

void MainWindow::setupCharts()
{
    m_pdrPointsShown = -1;
    m_throughputPointsShown = -1;
    m_delayPointsShown = -1;
    m_pdrSeries->clear();
    m_throughputSeries->clear();
    m_delaySeries->clear();

    double start = m_engine->startTime();
    double end = m_engine->endTime();
    setAxisRanges(m_pdrSeries, start, end, 100.0);
    setAxisRanges(m_throughputSeries, start, end, maxY(m_packetMetrics.throughputSeries()) * 1.15);
    setAxisRanges(m_delaySeries, start, end, maxY(m_packetMetrics.delaySeries()) * 1.15);

    m_chartsRow->setVisible(m_packetMetrics.hasPackets());
}

void MainWindow::buildScene()
{
    m_simBounds = m_engine->bounds();

    // Same scale for X and Y so distances are not distorted
    double largestSide = qMax(m_simBounds.width(), m_simBounds.height());
    if (largestSide <= 0.0) {
        largestSide = 1.0;
    }
    m_scale = NetworkDrawSize / largestSide;

    double areaWidth = m_simBounds.width() * m_scale;
    double areaHeight = m_simBounds.height() * m_scale;
    m_scene->setSceneRect(0, 0, areaWidth + 2 * ScenePadding, areaHeight + 2 * ScenePadding);

    // Network area background, slightly larger than the CSV bounds so nodes
    // on the edge are drawn fully inside it
    double margin = NodeRadius * 1.6;
    QRectF area(ScenePadding - margin, ScenePadding - margin,
                areaWidth + 2 * margin, areaHeight + 2 * margin);
    QGraphicsRectItem *areaItem = m_scene->addRect(area, QPen(QColor("#334155"), 2),
                                                   QBrush(QColor("#0f1a2e")));
    areaItem->setZValue(-2);

    // Grid lines with their NS-3 coordinate (metres) as labels
    double step = niceGridStep(largestSide);
    QPen gridPen(QColor("#1e293b"), 1);
    gridPen.setCosmetic(true);
    QFont labelFont;
    labelFont.setPixelSize(13);
    QColor labelColor("#64748b");

    double firstX = std::ceil(m_simBounds.left() / step) * step;
    for (double x = firstX; x <= m_simBounds.right(); x += step) {
        QPointF top = toScenePoint(QPointF(x, m_simBounds.bottom()));
        QPointF bottom = toScenePoint(QPointF(x, m_simBounds.top()));
        m_scene->addLine(QLineF(top, bottom), gridPen)->setZValue(-1);

        QGraphicsSimpleTextItem *label = m_scene->addSimpleText(QString::number(x), labelFont);
        label->setBrush(labelColor);
        label->setPos(bottom.x() - label->boundingRect().width() / 2, bottom.y() + 28);
    }

    double firstY = std::ceil(m_simBounds.top() / step) * step;
    for (double y = firstY; y <= m_simBounds.bottom(); y += step) {
        QPointF left = toScenePoint(QPointF(m_simBounds.left(), y));
        QPointF right = toScenePoint(QPointF(m_simBounds.right(), y));
        m_scene->addLine(QLineF(left, right), gridPen)->setZValue(-1);

        QGraphicsSimpleTextItem *label = m_scene->addSimpleText(QString::number(y), labelFont);
        label->setBrush(labelColor);
        label->setPos(left.x() - label->boundingRect().width() - 30,
                      left.y() - label->boundingRect().height() / 2);
    }

    // Links: one path for all links, one on top for the selected node's links
    QPen linkPen(QColor(56, 189, 248, 120), 1.5);
    linkPen.setCosmetic(true);
    m_linksItem = m_scene->addPath(QPainterPath(), linkPen);
    m_linksItem->setZValue(0);
    m_linksItem->setAcceptedMouseButtons(Qt::NoButton);

    QPen selectedLinkPen(QColor("#fbbf24"), 2.5);
    selectedLinkPen.setCosmetic(true);
    m_selectedLinksItem = m_scene->addPath(QPainterPath(), selectedLinkPen);
    m_selectedLinksItem->setZValue(0.5);
    m_selectedLinksItem->setAcceptedMouseButtons(Qt::NoButton);

    // Packets are drawn above the nodes so they stay visible
    m_packetsItem = m_scene->addPath(QPainterPath(), Qt::NoPen, QBrush(QColor("#4ade80")));
    m_packetsItem->setZValue(2);
    m_packetsItem->setAcceptedMouseButtons(Qt::NoButton);
    m_lostPacketsItem = m_scene->addPath(QPainterPath(), Qt::NoPen, QBrush(QColor("#f87171")));
    m_lostPacketsItem->setZValue(2);
    m_lostPacketsItem->setAcceptedMouseButtons(Qt::NoButton);

    // One range circle and one NodeItem per node ID found in the CSV
    const QList<int> ids = m_engine->nodeIds();
    for (int id : ids) {
        QColor color = nodeColor(id);

        QColor rangeLine = color;
        rangeLine.setAlpha(150);
        QColor rangeFill = color;
        rangeFill.setAlpha(22);
        QPen rangePen(rangeLine, 1.2, Qt::DashLine);
        rangePen.setCosmetic(true);

        QGraphicsEllipseItem *circle = m_scene->addEllipse(QRectF(), rangePen, QBrush(rangeFill));
        circle->setZValue(-0.5); // under links and nodes
        circle->setVisible(false);
        circle->setAcceptedMouseButtons(Qt::NoButton);
        m_rangeItems.insert(id, circle);

        NodeItem *item = new NodeItem(id, color, NodeRadius);
        m_scene->addItem(item);
        m_nodeItems.insert(id, item);
    }
}

void MainWindow::updateNetwork(double time)
{
    if (m_nodeItems.isEmpty() || !m_linksItem) {
        return;
    }

    QMap<int, QPointF> positions = m_engine->positionsAt(time);
    double rangeRadius = m_engine->communicationRange() * m_scale;
    bool showAllRanges = m_showAllRangesCheck->isChecked();

    // Nodes and their range circles
    for (auto it = positions.constBegin(); it != positions.constEnd(); ++it) {
        QPointF scenePos = toScenePoint(it.value());

        NodeItem *node = m_nodeItems.value(it.key());
        if (node) {
            node->setPos(scenePos);
        }

        QGraphicsEllipseItem *circle = m_rangeItems.value(it.key());
        if (circle) {
            circle->setRect(scenePos.x() - rangeRadius, scenePos.y() - rangeRadius,
                            2 * rangeRadius, 2 * rangeRadius);
            circle->setVisible(showAllRanges || it.key() == m_selectedNodeId);
        }
    }

    // Links between nodes that are currently within range
    QVector<NodeLink> links = m_engine->linksAt(time);
    QPainterPath otherLinks;
    QPainterPath selectedLinks;
    for (const NodeLink &link : links) {
        QPointF a = toScenePoint(positions.value(link.nodeA));
        QPointF b = toScenePoint(positions.value(link.nodeB));

        if (link.nodeA == m_selectedNodeId || link.nodeB == m_selectedNodeId) {
            selectedLinks.moveTo(a);
            selectedLinks.lineTo(b);
        } else {
            otherLinks.moveTo(a);
            otherLinks.lineTo(b);
        }
    }
    m_linksItem->setPath(otherLinks);
    m_selectedLinksItem->setPath(selectedLinks);
    m_linkCountLabel->setText(QString::number(links.size()));

    updatePackets(time, positions);
    updateInfoPanel(time);
}

void MainWindow::updatePackets(double time, const QMap<int, QPointF> &positions)
{
    QPainterPath delivered;
    QPainterPath lost;

    if (m_packetsToggle->isChecked() && m_packetMetrics.hasPackets()) {
        double speed = m_speedCombo->currentData().toDouble();
        double minTravelTime = PacketMinVisibleSeconds * speed; // in simulation seconds

        const QVector<PacketSample> &packets = m_packetMetrics.packets();
        const QVector<VisiblePacket> visible = m_packetMetrics.visiblePacketsAt(time, minTravelTime);
        for (const VisiblePacket &entry : visible) {
            const PacketSample &packet = packets[entry.index];
            if (!positions.contains(packet.source) || !positions.contains(packet.destination)) {
                continue;
            }

            QPointF from = toScenePoint(positions.value(packet.source));
            QPointF to = toScenePoint(positions.value(packet.destination));
            if (packet.received) {
                QPointF at = from + (to - from) * entry.progress;
                delivered.addEllipse(at, PacketDotRadius, PacketDotRadius);
            } else {
                // A lost packet never arrives: it only gets halfway, then disappears
                QPointF at = from + (to - from) * (0.5 * entry.progress);
                lost.addEllipse(at, PacketDotRadius, PacketDotRadius);
            }
        }
    }

    m_packetsItem->setPath(delivered);
    m_lostPacketsItem->setPath(lost);
}

static QString formatThroughput(double kbps)
{
    if (kbps >= 1000.0) {
        return QString::number(kbps / 1000.0, 'f', 2) + " Mbps";
    }
    return QString::number(kbps, 'f', 1) + " kbps";
}

void MainWindow::updateMetrics(double time)
{
    if (!m_packetMetrics.hasPackets()) {
        const QList<QLabel *> metricLabels = {m_pdrLabel, m_throughputLabel, m_delayLabel,
                                              m_sentLabel, m_receivedLabel, m_lostLabel};
        for (QLabel *label : metricLabels) {
            label->setText("–");
        }
        m_packetInfoLabel->setText("No packets.csv found next to the mobility CSV.");
        return;
    }

    MetricsSnapshot metrics = m_packetMetrics.snapshotAt(time);
    m_pdrLabel->setText(QString::number(metrics.pdrPercent, 'f', 1) + " %");
    m_throughputLabel->setText(formatThroughput(metrics.throughputKbps));
    m_delayLabel->setText(QString::number(metrics.averageDelayMs, 'f', 2) + " ms");
    m_sentLabel->setText(QString::number(metrics.sent));
    m_receivedLabel->setText(QString::number(metrics.received));
    m_lostLabel->setText(QString::number(metrics.lost));

    QString info = QString("In flight: %1").arg(metrics.inFlight);
    int last = m_packetMetrics.lastDeliveredAt(time);
    if (last >= 0) {
        const PacketSample &packet = m_packetMetrics.packets()[last];
        info += QString("     Last delivered: packet %1 (flow %2), node %3 → node %4, "
                        "sent %5 s, delay %6 ms, %7 bytes")
                    .arg(packet.packetId)
                    .arg(packet.flowId)
                    .arg(packet.source)
                    .arg(packet.destination)
                    .arg(QString::number(packet.sendTime, 'f', 3))
                    .arg(QString::number(1000.0 * (packet.receiveTime - packet.sendTime), 'f', 2))
                    .arg(packet.sizeBytes);
    }
    m_packetInfoLabel->setText(info);

    updateCharts(time);
}

// Shows the points of a graph up to 'time'. 'shown' remembers the last count so
// the series is only replaced when a new point appears.
static void showSeriesUpTo(QLineSeries *series, const QVector<QPointF> &points, double time,
                           int &shown)
{
    int count = 0;
    while (count < points.size() && points[count].x() <= time) {
        count++;
    }
    if (count != shown) {
        series->replace(points.mid(0, count));
        shown = count;
    }
}

void MainWindow::updateCharts(double time)
{
    showSeriesUpTo(m_pdrSeries, m_packetMetrics.pdrSeries(), time, m_pdrPointsShown);
    showSeriesUpTo(m_throughputSeries, m_packetMetrics.throughputSeries(), time,
                   m_throughputPointsShown);
    showSeriesUpTo(m_delaySeries, m_packetMetrics.delaySeries(), time, m_delayPointsShown);
}

void MainWindow::updateInfoPanel(double time)
{
    NodeItem *node = m_nodeItems.value(m_selectedNodeId, nullptr);
    if (!node) {
        m_infoDetails->hide();
        m_infoPlaceholder->show();
        return;
    }

    m_infoPlaceholder->hide();
    m_infoDetails->show();

    QPointF position = m_engine->positionAt(m_selectedNodeId, time);
    QList<int> neighbors = m_engine->neighborsAt(m_selectedNodeId, time);

    m_infoTitle->setText(QString("<span style='color:%1'>●</span>&nbsp; Node %2")
                             .arg(node->color().name())
                             .arg(m_selectedNodeId));
    m_infoX->setText(QString::number(position.x(), 'f', 2) + " m");
    m_infoY->setText(QString::number(position.y(), 'f', 2) + " m");
    m_infoTime->setText(formatTime(time));
    m_infoNeighborCount->setText(QString::number(neighbors.size()));
    m_infoRange->setText(QString::number(m_engine->communicationRange()) + " m");

    QStringList neighborIds;
    for (int id : neighbors) {
        neighborIds.append(QString::number(id));
    }
    m_infoNeighborList->setText(neighborIds.isEmpty() ? "None (isolated)" : neighborIds.join(", "));

    if (m_packetMetrics.hasPackets()) {
        m_infoPacketsSent->setText(QString::number(m_packetMetrics.sentByNode(m_selectedNodeId, time)));
        m_infoPacketsReceived->setText(
            QString::number(m_packetMetrics.receivedByNode(m_selectedNodeId, time)));
    } else {
        m_infoPacketsSent->setText("–");
        m_infoPacketsReceived->setText("–");
    }
}

QPointF MainWindow::toScenePoint(const QPointF &simPosition) const
{
    // NS-3 Y grows upwards, scene Y grows downwards, so Y is measured from maxY
    double sceneX = ScenePadding + (simPosition.x() - m_simBounds.left()) * m_scale;
    double sceneY = ScenePadding + (m_simBounds.bottom() - simPosition.y()) * m_scale;
    return QPointF(sceneX, sceneY);
}

void MainWindow::fitNetworkInView()
{
    if (!m_scene->sceneRect().isEmpty()) {
        m_view->fitInView(m_scene->sceneRect(), Qt::KeepAspectRatio);
    }
}

void MainWindow::setControlsEnabled(bool enabled)
{
    m_playButton->setEnabled(enabled && !m_engine->isPlaying());
    m_pauseButton->setEnabled(enabled && m_engine->isPlaying());
    m_resetButton->setEnabled(enabled);
    m_speedCombo->setEnabled(enabled);
    m_timelineSlider->setEnabled(enabled);
    m_rangeSpinBox->setEnabled(enabled);
    m_showAllRangesCheck->setEnabled(enabled);
    m_packetsToggle->setEnabled(enabled);
}

void MainWindow::openCsvDialog()
{
    QString startFolder = QFileInfo(QString(MANET_DEFAULT_CSV)).absolutePath();
    QString filePath = QFileDialog::getOpenFileName(this, "Open NS-3 mobility CSV", startFolder,
                                                    "CSV files (*.csv);;All files (*)");
    if (!filePath.isEmpty()) {
        loadCsv(filePath);
    }
}

void MainWindow::onTimeChanged(double time)
{
    updateNetwork(time);
    updateMetrics(time);

    m_timeLabel->setText(formatTime(time));
    m_sliderTimeLabel->setText(formatTime(time));

    // Move the slider without triggering onSliderValueChanged
    QSignalBlocker blocker(m_timelineSlider);
    m_timelineSlider->setValue(qRound((time - m_engine->startTime()) * 1000.0));
}

void MainWindow::onPlayingChanged(bool playing)
{
    m_playButton->setEnabled(m_dataLoaded && !playing);
    m_pauseButton->setEnabled(m_dataLoaded && playing);
}

void MainWindow::onSliderValueChanged(int value)
{
    m_engine->setTime(m_engine->startTime() + value / 1000.0);
}

void MainWindow::onSpeedChanged(int index)
{
    m_engine->setSpeed(m_speedCombo->itemData(index).toDouble());
}

void MainWindow::onRangeChanged(double metres)
{
    m_engine->setCommunicationRange(metres);
    updateNetwork(m_engine->currentTime());
}

void MainWindow::onSelectionChanged()
{
    m_selectedNodeId = -1;

    // Only NodeItems are selectable; use the first one if several are selected
    const QList<QGraphicsItem *> selected = m_scene->selectedItems();
    if (!selected.isEmpty()) {
        NodeItem *node = dynamic_cast<NodeItem *>(selected.first());
        if (node) {
            m_selectedNodeId = node->nodeId();
        }
    }

    updateNetwork(m_engine->currentTime());
}

void MainWindow::onPacketsToggled(bool on)
{
    m_packetsToggle->setText(on ? "●   Packets: ON" : "○   Packets: OFF");
    updateNetwork(m_engine->currentTime());
}

bool MainWindow::eventFilter(QObject *watched, QEvent *event)
{
    if (watched == m_view->viewport() && event->type() == QEvent::Resize) {
        fitNetworkInView();
    }
    return QMainWindow::eventFilter(watched, event);
}

QString MainWindow::formatTime(double seconds)
{
    return QString::number(seconds, 'f', 2) + " s";
}
