idf_component_register(
    SRCS "edge_core.c" "edge_monitor.c"
    INCLUDE_DIRS "include"
    REQUIRES vib_protocol esp_driver_gpio esp_driver_ledc esp_timer nvs_flash
)
