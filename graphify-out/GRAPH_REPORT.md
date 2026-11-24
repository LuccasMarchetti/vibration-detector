# Graph Report - vibration-detector  (2026-11-24)

## Corpus Check
- 46 files · ~31,422 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 5 file(s) not represented in the graph (top: (none) 3, .projbuild 1, .defaults 1)

## Summary
- 520 nodes · 1335 edges · 17 communities (14 shown, 3 thin omitted)
- Extraction: 90% EXTRACTED · 10% INFERRED · 0% AMBIGUOUS · INFERRED: 138 edges (avg confidence: 0.88)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `a5a0c334`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- test_edge.py
- acquisition.c
- StreamParser
- edge_native.py
- Guia rápido
- NodeState
- vib_sensor_t
- numpy
- edge_core.c
- MainWindow
- edge_monitor.c
- vib_protocol.c
- vib_protocol.h
- usb_link.c

## God Nodes (most connected - your core abstractions)
1. `MainWindow` - 31 edges
2. `NodeState` - 31 edges
3. `StreamParser` - 28 edges
4. `analyze()` - 26 edges
5. `SimulatedSource` - 25 edges
6. `Packet` - 23 edges
7. `main()` - 22 edges
8. `build_node()` - 22 edges
9. `AcquisitionHub` - 22 edges
10. `AlarmChecker` - 21 edges

## Surprising Connections (you probably didn't know these)
- `Alarme local: buzzer, saída de parada e botão (opcional)` --references--> `trip_output()`  [INFERRED]
  docs/PINAGEM.md → firmware/components/vib_edge/edge_monitor.c
- `6. Alarme no ESP32 (FFT no próprio ESP32)` --references--> `trip_output()`  [INFERRED]
  README.md → firmware/components/vib_edge/edge_monitor.c
- `vibration-detector — contexto para assistentes de IA` --references--> `probe()`  [INFERRED]
  CLAUDE.md → firmware/components/vib_sensors/vib_sensor.c
- `edge_task()` --calls--> `edge_core_mem_size()`  [INFERRED]
  firmware/components/vib_edge/edge_monitor.c → firmware/components/vib_edge/edge_core.c
- `nvs_load()` --calls--> `edge_default_config()`  [INFERRED]
  firmware/components/vib_edge/edge_monitor.c → firmware/components/vib_edge/edge_core.c

## Import Cycles
- None detected.

## Communities (17 total, 3 thin omitted)

### Community 0 - "test_edge.py"
Cohesion: 0.08
Nodes (28): _run(), _signal(), _StrongToneNode, test_alarm_hold_latch_and_reset(), test_c_baseline_deviation_matches_python_tracker(), test_c_command_parser_accepts_python_commands(), test_c_metrics_match_numpy(), test_config_json_and_validation() (+20 more)

### Community 1 - "acquisition.c"
Cohesion: 0.12
Nodes (8): edge_monitor_feed(), edge_monitor_set_sensor(), acquisition_start(), acquisition_task(), send_block(), setup_int1(), app_main(), node_id_detect()

### Community 2 - "StreamParser"
Cohesion: 0.09
Nodes (18): push(), link(), _feed(), test_alarm_hold_and_hook(), test_example_thresholds_file_loads(), test_node_gap_detection_and_analysis(), _data_pkt(), test_crc_little_endian_at_end() (+10 more)

### Community 3 - "edge_native.py"
Cohesion: 0.10
Nodes (8): available(), _build(), default_config(), error(), lib(), parse_commands(), validate_config(), _VirtualEdge

### Community 4 - "Guia rápido"
Cohesion: 0.07
Nodes (25): Alarme local: buzzer, saída de parada e botão (opcional), Diagrama de pinagem — ESP32 ↔ acelerômetro (LSM6DS3, BMI160 ou ICM-20948), Dicas de montagem (importantes para medir vibração de verdade), Nomes dos pinos em cada módulo, Alarme local (FFT no ESP32), Protocolo serial ESP32 ↔ PC (versão 1), 0. O que você precisa, 1. Instalar o ESP-IDF v6 (uma vez só) (+17 more)

### Community 5 - "NodeState"
Cohesion: 0.05
Nodes (16): test_record_and_export(), test_baseline_from_recording(), test_each_sensor_end_to_end(), run_gui(), AcquisitionHub, guess_esp32_ports(), list_serial_ports(), main() (+8 more)

### Community 6 - "vib_sensor_t"
Cohesion: 0.08
Nodes (38): graphify, vibration-detector — contexto para assistentes de IA, configure(), fifo_read(), range_code(), wr(), bank(), configure() (+30 more)

### Community 7 - "numpy"
Cohesion: 0.06
Nodes (35): test_impulsive_signal_raises_kurtosis(), test_metrics_sine_and_gaussian(), test_sine_amplitude_frequency_and_band_rms(), _baseline_and_node(), test_baseline_detects_new_fault(), test_short_capture_raises(), test_single_window_compare_still_works(), test_third_octave_bands() (+27 more)

### Community 8 - "edge_core.c"
Cohesion: 0.22
Nodes (15): bad(), edge_core_analyze(), edge_core_init(), edge_core_mem_size(), edge_core_reset_alarm(), edge_core_reset_baseline(), edge_default_config(), edge_validate_config() (+7 more)

### Community 11 - "MainWindow"
Cohesion: 0.06
Nodes (7): test_baseline_alarm_and_save_load(), test_pc_deviation_ignores_gui_window_and_unit(), AlarmChecker, AlarmEvent, Baseline, MainWindow, Spectrogram

### Community 13 - "edge_monitor.c"
Cohesion: 0.24
Nodes (8): baseline_ok(), buzzer(), edge_monitor_init(), nvs_load(), nvs_save(), outputs_init(), outputs_tick(), trip_output()

### Community 14 - "vib_protocol.c"
Cohesion: 0.26
Nodes (9): vib_rx_cb_t, vib_rx_parser_t, build_table(), drop(), vib_build_packet(), vib_crc16(), vib_rx_feed(), vib_rx_init() (+1 more)

### Community 15 - "vib_protocol.h"
Cohesion: 0.22
Nodes (6): edge_monitor_get_metrics(), vib_ack_payload_t, vib_edge_band_t, vib_edge_baseline_t, vib_edge_metrics_t, vib_status_payload_t

### Community 16 - "usb_link.c"
Cohesion: 0.39
Nodes (7): edge_monitor_command(), vib_header_t, on_command(), poll_rx(), send_packet(), send_status(), usb_task()

## Knowledge Gaps
- **11 isolated node(s):** `bandwidth_hz`, `chip_id`, `drv`, `fifo_capacity_samples`, `full_scale_g` (+6 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 155 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **3 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `trip_output()` connect `edge_monitor.c` to `Guia rápido`?**
  _High betweenness centrality (0.102) - this node is a cross-community bridge._
- **Are the 6 inferred relationships involving `MainWindow` (e.g. with `AlarmChecker` and `AlarmEvent`) actually correct?**
  _`MainWindow` has 6 INFERRED edges - model-reasoned connections that need verification._
- **What connects `bandwidth_hz`, `chip_id`, `drv` to the rest of the system?**
  _11 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `test_edge.py` be split into smaller, more focused modules?**
  _Cohesion score 0.08305084745762711 - nodes in this community are weakly interconnected._
- **Why does `6. Alarme no ESP32 (FFT no próprio ESP32)` connect `Guia rápido` to `edge_monitor.c`?**
  _High betweenness centrality (0.073) - this node is a cross-community bridge._
- **Are the 5 inferred relationships involving `NodeState` (e.g. with `_StrongToneNode` and `analyze()`) actually correct?**
  _`NodeState` has 5 INFERRED edges - model-reasoned connections that need verification._
- **Should `acquisition.c` be split into smaller, more focused modules?**
  _Cohesion score 0.1225296442687747 - nodes in this community are weakly interconnected._