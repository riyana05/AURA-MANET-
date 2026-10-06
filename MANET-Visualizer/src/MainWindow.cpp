#include "MainWindow.h"

#include "CsvLoader.h"
#include "NodeItem.h"
#include "SimulationEngine.h"

#include <QComboBox>
#include <QEvent>
#include <QFileDialog>
#include <QFileInfo>
#include <QFrame>
#include <QGraphicsRectItem>
#include <QGraphicsScene>
#include <QGraphicsSimpleTextItem>
#include <QGraphicsView>
#include <QHBoxLayout>
#include <QLabel>
#include <QMessageBox>
#include <QPushButton>
#include <QSignalBlocker>
#include <QSlider>
#include <QStatusBar>
#include <QVBoxLayout>

#include <cmath>

// The longer side of the network area is drawn this many scene units long.
// The view then scales the whole scene to fit the window.
static const double NetworkDrawSize = 900.0;
// Empty space around the network area (room for the axis labels)
static const double ScenePadding = 70.0;
static const double NodeRadius = 14.0;

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

QComboBox {
    background: #1e293b; color: #e2e8f0;
    border: 1px solid #334155; border-radius: 8px; padding: 6px 10px;
}
QComboBox:disabled { color: #475569; }
QComboBox QAbstractItemView {
    background: #1e293b; color: #e2e8f0; selection-background-color: #0ea5e9;
}

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

    m_nodeCountLabel = new QLabel("–");
    m_timeLabel = new QLabel("–");

    m_openButton = new QPushButton("Open CSV…");

    sideLayout->addWidget(simulationHeading);
    sideLayout->addSpacing(4);
    sideLayout->addWidget(m_playButton);
    sideLayout->addWidget(m_pauseButton);
    sideLayout->addWidget(m_resetButton);
    sideLayout->addSpacing(12);
    sideLayout->addWidget(speedCaption);
    sideLayout->addWidget(m_speedCombo);
    sideLayout->addSpacing(12);
    sideLayout->addWidget(makeStatCard("Nodes", m_nodeCountLabel));
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
    middleLayout->addWidget(m_view, 1);

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

bool MainWindow::loadCsv(const QString &filePath)
{
    CsvLoader loader;
    if (!loader.load(filePath)) {
        QMessageBox::warning(this, "Could not load CSV", loader.errorString());
        return false;
    }

    // Remove the previous simulation's items before the engine announces the new data
    m_scene->clear();
    m_nodeItems.clear();

    m_engine->setSamples(loader.samples());
    buildScene();

    double duration = m_engine->endTime() - m_engine->startTime();
    {
        QSignalBlocker blocker(m_timelineSlider);
        // The slider works in milliseconds from the first timestamp
        m_timelineSlider->setRange(0, qRound(duration * 1000.0));
    }
    m_startTimeLabel->setText(formatTime(m_engine->startTime()));
    m_endTimeLabel->setText(formatTime(m_engine->endTime()));
    m_nodeCountLabel->setText(QString::number(m_engine->nodeCount()));
    m_fileLabel->setText(QFileInfo(filePath).fileName());

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
    statusBar()->showMessage(message);
    return true;
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

    // One NodeItem per node ID found in the CSV
    const QList<int> ids = m_engine->nodeIds();
    for (int id : ids) {
        NodeItem *item = new NodeItem(id, nodeColor(id), NodeRadius);
        m_scene->addItem(item);
        m_nodeItems.insert(id, item);
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
    for (NodeItem *item : m_nodeItems) {
        item->setPos(toScenePoint(m_engine->positionAt(item->nodeId(), time)));
    }

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
