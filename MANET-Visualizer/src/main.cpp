#include "MainWindow.h"

#include <QApplication>
#include <QFile>

int main(int argc, char *argv[])
{
    QApplication app(argc, argv);
    app.setStyle("Fusion");

    MainWindow window;
    window.resize(1280, 820);
    window.show();

    // Usage: MANETVisualizer [path/to/mobility.csv]
    QStringList arguments = app.arguments();
    QString csvPath = arguments.size() > 1 ? arguments.at(1) : QString(MANET_DEFAULT_CSV);
    if (QFile::exists(csvPath)) {
        window.loadCsv(csvPath);
    }

    return app.exec();
}
