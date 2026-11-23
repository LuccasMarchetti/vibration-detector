/*
 * node_id.c — Identificacao do no (MANCAL_1 / MANCAL_2)
 *
 * Padrao: GPIO CONFIG_VIB_PIN_NODE_SEL com pull-up interno.
 *   - pino aberto (nivel 1)      -> MANCAL_1
 *   - jumper do pino para GND (0) -> MANCAL_2
 * Pode ser forcado via menuconfig (CONFIG_VIB_NODE_ID_FORCED = 1 ou 2).
 * Assim os DOIS ESP32 usam exatamente o mesmo binario.
 */
#include "driver/gpio.h"
#include "esp_rom_sys.h"
#include "sdkconfig.h"
#include "app.h"

uint8_t node_id_detect(void)
{
#if CONFIG_VIB_NODE_ID_FORCED != 0
    return CONFIG_VIB_NODE_ID_FORCED;
#else
    gpio_config_t io = {
        .pin_bit_mask = 1ULL << CONFIG_VIB_PIN_NODE_SEL,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    gpio_config(&io);
    esp_rom_delay_us(1000); /* deixa o pull-up estabilizar */
    int votes = 0;
    for (int i = 0; i < 8; i++) {
        votes += gpio_get_level(CONFIG_VIB_PIN_NODE_SEL);
        esp_rom_delay_us(100);
    }
    return (votes >= 4) ? 1 : 2;
#endif
}
