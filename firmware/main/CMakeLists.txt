idf_component_register(
    SRCS "main.c" "acquisition.c" "usb_link.c" "node_id.c"
    INCLUDE_DIRS "."
    REQUIRES lsm6ds3 vib_protocol esp_driver_gpio esp_driver_uart esp_timer nvs_flash
)
