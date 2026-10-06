#include "MainWindow.h"

#include <QApplication>
#include <QFile>
#include <QScreen>

int main(int argc, char *argv[])
{
    QApplication app(argc, argv);
    app.setStyle("Fusion");

    MainWindow window;
    // Large enough for the network, the metrics panel and both side panels
    QRect screen = window.screen()->availableGeometry();
    window.resize(qMin(1600, int(screen.width() * 0.95)), qMin(1000, int(screen.height() * 0.95)));
    window.show();

    // Usage: MANETVisualizer [path/to/mobility.csv]
    QStringList arguments = app.arguments();
    QString csvPath = arguments.size() > 1 ? arguments.at(1) : QString(MANET_DEFAULT_CSV);
    if (QFile::exists(csvPath)) {
        window.loadCsv(csvPath);
    }

    return app.exec();
}
