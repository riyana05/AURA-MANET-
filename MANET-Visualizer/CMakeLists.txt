cmake_minimum_required(VERSION 3.16)

project(MANETVisualizer VERSION 0.1 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Runs Qt's moc on classes that use Q_OBJECT (signals/slots)
set(CMAKE_AUTOMOC ON)

find_package(Qt6 REQUIRED COMPONENTS Widgets Charts)

add_executable(MANETVisualizer
    src/main.cpp
    src/MainWindow.cpp
    src/CsvLoader.cpp
    src/SimulationEngine.cpp
    src/NodeItem.cpp
    src/PacketMetrics.cpp
    include/MainWindow.h
    include/CsvLoader.h
    include/SimulationEngine.h
    include/NodeItem.h
    include/PacketMetrics.h
)

target_include_directories(MANETVisualizer PRIVATE include)

target_link_libraries(MANETVisualizer PRIVATE Qt6::Widgets Qt6::Charts)

# CSV that is opened automatically at startup (another file can be opened from the GUI).
# packets.csv is loaded from the same folder as the mobility CSV when it exists.
target_compile_definitions(MANETVisualizer PRIVATE
    MANET_DEFAULT_CSV="${CMAKE_CURRENT_SOURCE_DIR}/data/node_mobility.csv"
)
