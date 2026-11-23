# vibration-detector

Monitoramento de vibração em tempo real dos **dois mancais do motor do túnel de vento**.

- 2 nós idênticos **ESP32 + LSM6DS3** (um por mancal), ligados por **USB** ao mesmo PC.
- Aquisição a **6,66 kHz** por eixo (X, Y, Z) usando a FIFO do sensor + interrupção `INT1`.
- Software no PC: leitura das duas portas em paralelo, remoção de DC, **FFT**, **RMS / pico a pico / curtose**,
  gráficos em tempo real, **gravação de baseline** com anotação de rotação e **ganchos para alarmes**.

```
 Mancal 1: LSM6DS3 ─SPI 8 MHz + INT1─► ESP32 ─USB 921600─┐
                                                        ├─► PC: python -m vibmon (gráficos, FFT, gravação)
 Mancal 2: LSM6DS3 ─SPI 8 MHz + INT1─► ESP32 ─USB 921600─┘
```

| Pasta | Conteúdo |
|-------|----------|
| [`docs/PINAGEM.md`](docs/PINAGEM.md) | **Diagrama de pinagem** ESP32 ↔ LSM6DS3 + dicas de montagem |
| [`docs/PROTOCOLO.md`](docs/PROTOCOLO.md) | Formato dos pacotes USB (cabeçalho, timestamp, CRC) |
| `firmware/` | Projeto **ESP-IDF v6** (C) — o mesmo binário serve para os dois mancais |
| `pc/` | Aplicação Python (`vibmon`) + testes |
| `graphify-out/`, `CLAUDE.md` | Grafo de conhecimento do código para assistentes de IA (ver seção *graphify*) |

---

## Guia rápido

### 0. O que você precisa

- 2× ESP32 DevKitC (WROOM-32), 2× módulo LSM6DS3, jumpers, 2 cabos USB **de dados**.
- PC com Windows, Linux ou macOS e Python 3.10 ou mais novo.
- Monte a fiação conforme [`docs/PINAGEM.md`](docs/PINAGEM.md). No ESP32 do **Mancal 2**, ligue o
  **GPIO27 ao GND** com um jumper. No Mancal 1, deixe o GPIO27 livre.

### 1. Instalar o ESP-IDF v6 (uma vez só)

Siga o guia oficial *Get Started* da Espressif para a versão **v6.x**
(<https://docs.espressif.com/projects/esp-idf/en/stable/esp32/get-started/>):

- **Windows:** use o instalador oficial (ESP-IDF Installation Manager) e abra o terminal “ESP-IDF” que ele cria.
- **Linux/macOS:**

```bash
git clone -b v6.1.1 --recursive https://github.com/espressif/esp-idf.git ~/esp/esp-idf
```

```bash
~/esp/esp-idf/install.sh esp32
```

Em **todo terminal novo** (Linux/macOS), ative o ambiente antes de usar o `idf.py`:

```bash
. ~/esp/esp-idf/export.sh
```

> Alternativa sem linha de comando: extensão **ESP-IDF** do VS Code (ela instala e compila pelos botões da barra inferior).

### 2. Compilar e gravar o firmware

```bash
cd firmware
```

```bash
idf.py set-target esp32
```

```bash
idf.py build
```

Conecte **um** ESP32 por vez e grave (troque a porta: `COM3` no Windows, `/dev/ttyUSB0` no Linux,
`/dev/cu.usbserial-XXXX` no macOS):

```bash
idf.py -p COM3 flash
```

Repita com o segundo ESP32 — é o **mesmo** firmware; o jumper no GPIO27 é que define qual é o Mancal 2.

> Se a gravação não começar, segure o botão **BOOT** da placa enquanto aparece `Connecting....`.
>
> `idf.py monitor` vai mostrar “lixo”: a USB transmite dados **binários**. Para ver os dados, use o `vibmon` (passo 4).

Configurações opcionais (pinos, fundo de escala ±4/8/16 g, watermark 512/1024, baud rate):

```bash
idf.py menuconfig
```

Menu **Vibration Node (Mancal)**. Se mudar o baud rate, use o mesmo valor no `--baud` do `vibmon`.

### 3. Instalar o software do PC

```bash
cd pc
```

```bash
python -m venv .venv
```

Ative o ambiente virtual — Windows: `.venv\Scripts\activate` · Linux/macOS: `source .venv/bin/activate`. Depois:

```bash
pip install -r requirements.txt
```

### 4. Rodar

Teste **sem hardware** primeiro (dois mancais simulados; o Mancal 2 simula um defeito de rolamento):

```bash
python -m vibmon --simulate
```

Com os ESP32 conectados, liste as portas e abra as duas (a ordem não importa: cada ESP32 informa
se é o Mancal 1 ou 2):

```bash
python -m vibmon --list-ports
```

```bash
python -m vibmon COM3 COM4
```

Sem argumentos, o `vibmon` tenta encontrar as portas sozinho (`python -m vibmon`).

**Na janela:**

| Elemento | Função |
|----------|--------|
| Gráficos superiores | Aceleração no tempo (X, Y, Z) de cada mancal — últimos 0,5 s |
| Gráfico inferior | Espectro (FFT) do Mancal 1 × Mancal 2, com o pico dominante marcado |
| Painéis à direita | RMS, pico a pico, curtose, frequência dominante por eixo, RMS global e diagnóstico do link |
| `Janela FFT` / tipo | 1024–8192 amostras; janela Hann, Hamming ou retangular |
| `Eixo` / `Unidade` | eixo mostrado na FFT; `g` ou `m/s²` |
| `● Gravar` | inicia/para a gravação da sessão em `pc/recordings/` |
| `RPM`, `vel. m/s`, observação, `Anotar` | registra a condição do túnel **durante** a gravação |

Sem interface gráfica (ex.: PC do laboratório via SSH), imprimindo as métricas a cada segundo e gravando por 60 s:

```bash
python -m vibmon COM3 COM4 --headless --record ensaio_1200rpm --duration 60
```

### 5. Exportar as gravações

Cada sessão fica em `pc/recordings/AAAAMMDD_HHMMSS_rotulo/` com `mancal_1.vib`, `mancal_2.vib`
(binário compacto, sem perdas, ~41 kB/s por mancal), `annotations.csv` e `session.json`. Para CSV
(ou `--format npz` para NumPy):

```bash
python -m vibmon.export recordings/20261123_143000_ensaio_1200rpm
```

Colunas: `sample_index, t_s, x_g, y_g, z_g`. Saltos em `sample_index` indicam amostras perdidas.

### 6. Problemas comuns

| Sintoma na GUI | Causa provável |
|----------------|----------------|
| “sem dados (cabo USB / porta?)” | porta errada, cabo só de carga, baud diferente do firmware, porta aberta em outro programa |
| “sensor LSM6DS3 NÃO respondeu” | fiação SPI/alimentação; teste clock SPI de 4 MHz no menuconfig |
| “portas X e Y dizem ser MANCAL_n” | jumper do GPIO27 igual nas duas placas |
| contador `CRC` subindo | cabo USB ruim/longo ou ruído; tente 921600 se estiver em 2 Mbps |
| `perdidas` / `overrun` / `drops` > 0 | PC sobrecarregado ou USB lenta; feche outros programas |
| Linux: “Permission denied” na porta | `sudo usermod -aG dialout $USER` e faça logout/login |

---

## Arquitetura

### Firmware (ESP-IDF v6, FreeRTOS dual-core)

```
LSM6DS3 FIFO (contínua, ODR 6,66 kHz, watermark 512 amostras ≈ 77 ms)
   │ INT1 sobe
   ▼
ISR (IRAM) ── notifica ──► acquisition_task  [Core 1, prioridade máx-2]
                             lê FIFO_STATUS, alinha no eixo X,
                             SPI burst read (DMA) de toda a FIFO → bloco do pool
                             │ fila de ponteiros (zero-cópia)
                             ▼
                           usb_task  [Core 0]
                             cabeçalho + ID do mancal + timestamp µs + CRC16 → UART0/USB
                             pacote STATUS a cada 1 s (heartbeat + diagnóstico)
```

- **Nunca deixa a FIFO do sensor transbordar:** se o PC/USB atrasar e o pool de blocos encher, a FIFO é
  drenada mesmo assim e o pacote seguinte sai com a flag `BLOCK_DROP`; o índice `first_sample`
  continua contando, então o PC sabe exatamente quantas amostras faltaram.
- Banda necessária ≈ 40 kB/s por mancal; a 921 600 bps a UART tem ~2× de folga.
- Compilado e verificado com **ESP-IDF v6.1.1** (alvo `esp32`).

### Software do PC (`pc/vibmon`)

| Módulo | Papel |
|--------|-------|
| `protocol.py` | parser incremental com ressincronização e verificação de CRC |
| `sources.py` | 1 thread por porta serial (pyserial libera o GIL) + simulador que gera o mesmo fluxo de bytes |
| `hub.py` / `node.py` | identifica a porta de cada mancal; buffer circular de 10 s; perdas, `seq`, taxa real estimada |
| `dsp.py` / `analysis.py` | remoção de DC, janela Hann/Hamming, FFT com amplitude corrigida, RMS por banda, RMS, pico a pico, curtose, fator de crista |
| `alarms.py` | **gancho de limiares** (RMS, curtose, RMS em banda de frequência…) com debounce e callbacks |
| `recorder.py` / `export.py` | gravação binária sem perdas + anotações; exportação para CSV/NPZ |
| `gui.py` | interface em tempo real (pyqtgraph) |

### Da baseline aos alarmes

1. Grave sessões com o motor **em bom estado** em várias rotações (anote RPM/velocidade em cada patamar).
2. Exporte e calcule, por rotação, a média e o desvio padrão do RMS global, do RMS por banda
   (ex.: 1× rotação, 2×, banda alta de rolamento) e da curtose.
3. Copie `pc/config/thresholds.example.json`, ajuste os limites (ex.: média + 3σ como alerta; normas como a
   ISO 20816 ajudam a escolher os níveis) e mude `"enabled": true`.
4. Rode com `python -m vibmon --thresholds config/meus_limites.json`. A GUI pinta o painel do mancal e emite um
   bipe; para intertravamento (relé/CLP), adicione um callback em `AlarmChecker.hooks` (ver `alarms.py`).

### Banda útil do sensor (conferida no AN4650 da ST)

Com ODR de 6,66 kHz, o LSM6DS3 original (WHO_AM_I = 0x69) tem **banda total de 3160 Hz** (AN4650 Rev 2,
tabela 9). O motivo é que, a 3,33 e 6,66 kHz, o **filtro anti-aliasing analógico não é usado** quando
`XL_BW_SCAL_ODR = 0` (tabela 7). Sobra só o filtro digital LPF1, com corte em 3320 Hz (tabela 8).

O firmware deixa `XL_BW_SCAL_ODR = 0` de propósito. Com esse bit em 1, a banda analógica passaria a ser
definida por `BW_XL`, cujo máximo é **400 Hz** (tabela 6). Isso eliminaria justamente as frequências de
defeito de rolamento.

Como não há filtro analógico antes do conversor nesse modo, vibrações muito acima de ~3,3 kHz (por exemplo,
impactos metálicos ou a ressonância da fixação) podem aparecer **rebatidas** (aliasing) dentro do espectro.
A nota da ST não informa a ordem do LPF1. Na prática, vale validar com um sinal de referência (um *shaker*,
ou um motor girando a uma rotação conhecida) e desconfiar de picos que mudam de frequência de forma estranha
quando a rotação muda.

O LSM6DS3TR-C (0x6A) também é suportado. A FIFO dele é de 4 kB, então o watermark fica limitado a ~600 amostras.

---

## Testes

```bash
cd pc
```

```bash
pip install -r requirements-dev.txt
```

```bash
python -m pytest -q
```

Os testes cobrem o protocolo (incluindo o vetor de CRC usado pelo firmware em C), ressincronização após lixo
do bootloader, FFT/métricas com sinais conhecidos, detecção de perdas, alarmes e gravação → exportação.

---

## graphify — economizar tokens com assistentes de IA

> **graphify não é uma biblioteca de gráficos.** É uma ferramenta que transforma o código do repositório num
> **grafo de conhecimento** consultável (`graphify-out/graph.json`). Assistentes como o Claude Code consultam o
> grafo em vez de ler arquivos inteiros, o que reduz bastante o consumo de tokens. Por isso os gráficos de
> vibração usam **pyqtgraph**, e o graphify foi configurado para o seu propósito real.

O que já está pronto no repositório:

- `graphify-out/graph.json` e `GRAPH_REPORT.md` — grafo do firmware + software (gerado só por análise de código
  local, sem LLM e sem enviar nada para fora).
- `CLAUDE.md` — resumo do projeto + regras para o assistente usar `graphify query/explain/path` antes de abrir arquivos.
- `.claude/settings.json` — hooks do Claude Code que lembram o assistente de consultar o grafo.
- `.graphifyignore` — exclui build, gravações e binários do grafo.

Para usar na sua máquina:

```bash
pip install graphifyy
```

Exemplos de consulta:

```bash
graphify query "como a FIFO do sensor é lida"
```

```bash
graphify explain "StreamParser"
```

```bash
graphify path "acquisition_task" "send_packet"
```

Depois de alterar o código, atualize o grafo (rápido, sem custo de API):

```bash
graphify update .
```
