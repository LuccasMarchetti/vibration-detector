idf_component_register(
    SRCS "lsm6ds3.c"
    INCLUDE_DIRS "include"
    REQUIRES esp_driver_spi esp_driver_gpio
)
