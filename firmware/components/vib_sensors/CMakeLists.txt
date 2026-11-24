idf_component_register(
    SRCS "vib_sensor.c" "lsm6ds3.c" "bmi160.c" "icm20948.c"
    INCLUDE_DIRS "include"
    PRIV_INCLUDE_DIRS "."
    REQUIRES esp_driver_spi esp_driver_gpio
)
