##
# Cura T5AI CMakeLists.txt — TuyaOpen project format
##

set(APP_PATH ${CMAKE_CURRENT_LIST_DIR})
get_filename_component(APP_NAME ${APP_PATH} NAME)

aux_source_directory(${APP_PATH}/src APP_SRC)

set(APP_INC  ${APP_PATH}/src)
set(APP_OPTIONS "-Wall" "-DLV_LVGL_H_INCLUDE_SIMPLE")

########################################
# Target Configure
########################################
add_library(${EXAMPLE_LIB})

target_sources(${EXAMPLE_LIB}
    PRIVATE
        ${APP_SRC}
)

target_include_directories(${EXAMPLE_LIB}
    PRIVATE
        ${APP_INC}
)

target_compile_options(${EXAMPLE_LIB}
    PRIVATE
        ${APP_OPTIONS}
)
