cmake_minimum_required(VERSION 3.16)
project(FlyStressCapture)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_AUTOMOC ON)

find_package(Qt6 REQUIRED COMPONENTS Widgets)
find_package(OpenCV REQUIRED)

include_directories(/home/admin/Downloads/SVBCameraSDK/include)
link_directories(/home/admin/Downloads/SVBCameraSDK/lib/armv8)

add_executable(FlyStressCapture main.cpp)

target_link_libraries(FlyStressCapture
    Qt6::Widgets
    ${OpenCV_LIBS}
    SVBCameraSDK
    usb-1.0
    pthread
)
