cmake_minimum_required(VERSION 3.13)

# PICO_BOARD por defecto: "pico". Ajustar si la flash del DSPico lo requiere.
set(PICO_BOARD pico CACHE STRING "Board type")

include(pico_sdk_import.cmake)

project(fbdump C CXX ASM)
set(CMAKE_C_STANDARD 11)

pico_sdk_init()

add_executable(fbdump
    src/main.c
    src/usb/fbdump_class.c
    src/usb/fbdump_control.c
    src/usb/usb_descriptors.c
    src/speedtest/speedtest.c
)

target_include_directories(fbdump PRIVATE
    src
    src/usb # tusb_config.h
    ${CMAKE_CURRENT_LIST_DIR}/../common/include
)

target_compile_options(fbdump PRIVATE -Wall -Wextra -Werror)

target_link_libraries(fbdump PRIVATE
    pico_stdlib
    pico_unique_id
    hardware_clocks
    tinyusb_device
)

# Sin stdio: UART0 (GPIO0/1) podría chocar con los pines del DSPico.
pico_enable_stdio_uart(fbdump 0)
pico_enable_stdio_usb(fbdump 0)

pico_add_extra_outputs(fbdump)
