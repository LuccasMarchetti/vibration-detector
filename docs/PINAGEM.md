# Diagrama de pinagem — ESP32 ↔ LSM6DS3

Os **dois nós são idênticos** (mesma fiação e mesmo firmware). A única diferença
é o **jumper de identificação** no GPIO27.

```
            ESP32 DevKitC (WROOM-32)                     Módulo LSM6DS3
          ┌──────────────────────────┐                ┌─────────────────┐
          │                      3V3 ├────────────────┤ VCC / VIN       │
          │                      GND ├────────┬───────┤ GND             │
          │                          │        │       │                 │
          │  GPIO18  (VSPI SCLK)     ├────────┼───────┤ SCL  = SPC      │
          │  GPIO23  (VSPI MOSI)     ├────────┼───────┤ SDA  = SDI      │
          │  GPIO19  (VSPI MISO)     ├────────┼───────┤ SDO  = SA0      │
          │  GPIO5   (VSPI CS)       ├────────┼───────┤ CS              │
          │  GPIO4   (entrada IRQ)   ├────────┼───────┤ INT1            │
          │                          │        │       │ INT2  (n/c)     │
          │  GPIO27  (ID do mancal)  ├──┐     │       └─────────────────┘
          │                          │  │     │
          │               USB (UART0)├──┼─────┼────── cabo USB ──► PC
          └──────────────────────────┘  │     │
                                        │     │
             MANCAL_1:  GPIO27 aberto ──┘     │   (pull-up interno = nível 1)
             MANCAL_2:  GPIO27 ──[jumper]─────┘   (ligado ao GND = nível 0)
```

| Sinal         | ESP32  | LSM6DS3 (serigrafia comum) | Observação |
|---------------|--------|----------------------------|------------|
| Alimentação   | 3V3    | VCC / VIN                  | **Somente 3,3 V** |
| Terra         | GND    | GND                        | terra comum |
| SPI clock     | GPIO18 | SCL / SPC                  | modo SPI 3, 8 MHz (máx. 10 MHz) |
| SPI MOSI      | GPIO23 | SDA / SDI                  | |
| SPI MISO      | GPIO19 | SDO / SA0                  | |
| Chip select   | GPIO5  | CS                         | CS em nível baixo seleciona o modo SPI |
| Interrupção   | GPIO4  | INT1                       | watermark da FIFO, ativo em nível alto |
| ID do mancal  | GPIO27 | —                          | aberto = MANCAL_1, jumper p/ GND = MANCAL_2 |

Todos os pinos podem ser alterados em `idf.py menuconfig` → *Vibration Node (Mancal)*.
GPIO18/19/23/5 são os pinos nativos (IO_MUX) do VSPI — mantenha-os para usar 8–10 MHz com folga.

## Dicas de montagem (importantes para medir vibração de verdade)

1. **Fixação rígida do sensor** no mancal: parafuso/prisioneiro ou base metálica colada com
   epóxi/cianoacrilato. Fita dupla-face e abraçadeira amortecem as altas frequências.
2. Anote a **orientação dos eixos** (X/Y/Z impressos no módulo) em relação ao eixo do motor
   (radial horizontal, radial vertical, axial) e use a **mesma orientação** nos dois mancais.
3. Fios SPI **curtos** (< 20 cm), de preferência trançados com GND. Se o `who_am_i` vier 0
   (GUI mostra “sensor NÃO respondeu”), confira a fiação ou reduza o clock para 4 MHz no menuconfig.
4. Prenda os cabos (alívio de tensão) — vibração solta conectores Dupont.
5. Use cabos USB de dados (não “só carga”) e, se possível, um hub USB com alimentação.
