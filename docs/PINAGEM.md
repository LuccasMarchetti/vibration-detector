# Diagrama de pinagem — ESP32 ↔ acelerômetro (LSM6DS3, BMI160 ou ICM-20948)

Os **dois nós são idênticos** (mesma fiação e mesmo firmware). A única diferença
é o **jumper de identificação** no GPIO27.

O firmware **detecta sozinho** qual dos três sensores está ligado. A fiação é a mesma:
só muda o nome que cada fabricante de módulo imprime nos pinos (tabela no fim desta página).
Os dois mancais podem até usar sensores diferentes, mas para comparar um com o outro
prefira o mesmo modelo nos dois.

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

## Alarme local: buzzer, saída de parada e botão (opcional)

O ESP32 faz a FFT e as regras de alarme sozinho (ver README, “Alarme no ESP32”). Para ele avisar sem o PC:

```
  ESP32 GPIO25 ──────────────────► buzzer (+)      buzzer (−) ── GND
                                    passivo ou ativo; até ~20 mA direto no pino,
                                    acima disso use um transistor (ex.: 2N2222 + resistor de 1 kΩ na base)

  ESP32 GPIO26 ──► IN do módulo relé / optoacoplador ──► contato ──► entrada de PARADA/HABILITA do inversor
                   (alimentado por 5 V ou 3,3 V conforme o módulo)

  Botão BOOT da placa (GPIO0) = reconhecer: silencia o buzzer e rearma a parada travada
```

| Sinal | ESP32 | Observação |
|-------|-------|------------|
| Buzzer | GPIO25 | `-1` no menuconfig desativa; tom de 2,7 kHz por PWM (buzzer passivo) |
| Saída de parada | GPIO26 | só atua se `trip_output` estiver habilitado na configuração enviada pelo PC (**desligado por padrão**) |
| Reconhecer / rearmar | GPIO0 (botão BOOT) | pode trocar por um botão externo para GND |

> ⚠️ **A saída de parada NÃO é uma função de segurança certificada.** A parada de emergência do túnel
> continua sendo o circuito físico existente. Nunca ligue o motor ou a rede elétrica direto no ESP32:
> use um módulo relé ou optoacoplador atuando na **entrada de comando** do inversor/contator.
>
> Polaridade (menuconfig → *Alarme local*): por padrão, **nível alto = parar**. A opção *fail-safe*
> (desmarcar “Nível ALTO = parar o motor”) mantém a saída em 3,3 V com o motor liberado. Assim, um ESP32
> desligado, travado ou com cabo solto também para o motor. É a opção recomendada depois que os limites
> estiverem validados, mas qualquer reinício do ESP32 vai parar o túnel.

## Nomes dos pinos em cada módulo

| ESP32  | Função SPI | LSM6DS3        | BMI160 (ex.: GY-BMI160) | ICM-20948 (ex.: SparkFun / genéricos) |
|--------|------------|----------------|-------------------------|---------------------------------------|
| 3V3    | alimentação| VCC / VIN      | VIN / 3V3               | VIN / 3V3                             |
| GND    | terra      | GND            | GND                     | GND                                   |
| GPIO18 | SCLK       | SCL / SPC      | SCL                     | SCL / SCLK                            |
| GPIO23 | MOSI       | SDA / SDI      | SDA                     | SDA / SDI                             |
| GPIO19 | MISO       | SDO / SA0      | SAO / SDO               | AD0 / SDO                             |
| GPIO5  | CS         | CS             | CS                      | CS / NCS                              |
| GPIO4  | interrupção| INT1           | INT1                    | *não usado* (pode deixar ligado ao INT) |

- **ICM-20948:** não tem nível de watermark configurável, então o firmware lê a FIFO
  por *polling* a cada 5 ms e o pino de interrupção fica sem uso. O SPI fica limitado a 7 MHz.
- **BMI160:** precisa de uma leitura “dummy” para entrar em modo SPI. O firmware já faz isso.
- Confira se o módulo tem regulador de tensão. Os três chips são de **3,3 V**; alimente pelo 3V3 do ESP32.
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
