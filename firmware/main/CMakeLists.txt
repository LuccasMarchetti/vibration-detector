idf_component_register(
    SRCS "main.c" "acquisition.c" "usb_link.c" "node_id.c"
    INCLUDE_DIRS "."
    REQUIRES vib_sensors vib_protocol esp_driver_gpio esp_driver_uart esp_timer
)
