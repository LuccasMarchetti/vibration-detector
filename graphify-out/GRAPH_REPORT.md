# Graph Report - vibration-detector  (2026-11-24)

## Corpus Check
- 37 files · ~18,878 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 5 file(s) not represented in the graph (top: (none) 3, .projbuild 1, .defaults 1)

## Summary
- 359 nodes · 866 edges · 13 communities (10 shown, 3 thin omitted)
- Extraction: 90% EXTRACTED · 10% INFERRED · 0% AMBIGUOUS · INFERRED: 83 edges (avg confidence: 0.88)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `7cc9feea`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- gui.py
- acquisition.c
- SimulatedSource
- test_sensors_baseline.py
- Guia rápido
- main
- vib_sensor_t
- numpy
- vib_sensor
- MainWindow

## God Nodes (most connected - your core abstractions)
1. `NodeState` - 24 edges
2. `MainWindow` - 23 edges
3. `main()` - 22 edges
4. `analyze()` - 21 edges
5. `SimulatedSource` - 20 edges
6. `AlarmChecker` - 19 edges
7. `StreamParser` - 19 edges
8. `vib_sensor_t` - 18 edges
9. `AcquisitionHub` - 18 edges
10. `Packet` - 17 edges

## Surprising Connections (you probably didn't know these)
- `vibration-detector — contexto para assistentes de IA` --references--> `probe()`  [INFERRED]
  CLAUDE.md → firmware/components/vib_sensors/vib_sensor.c
- `acquisition_task()` --calls--> `vib_sensor_fifo_read()`  [INFERRED]
  firmware/main/acquisition.c → firmware/components/vib_sensors/include/vib_sensor.h
- `acquisition_start()` --calls--> `vib_sensor_init()`  [INFERRED]
  firmware/main/acquisition.c → firmware/components/vib_sensors/vib_sensor.c
- `main()` --uses--> `Baseline`  [INFERRED]
  pc/vibmon/__main__.py → pc/vibmon/baseline.py
- `MainWindow` --uses--> `AlarmEvent`  [INFERRED]
  pc/vibmon/gui.py → pc/vibmon/alarms.py

## Import Cycles
- None detected.

## Communities (13 total, 3 thin omitted)

### Community 0 - "gui.py"
Cohesion: 0.11
Nodes (9): test_alarm_hold_and_hook(), test_example_thresholds_file_loads(), AlarmChecker, AlarmEvent, Threshold, _value(), NodeAnalysis, DeviationTracker (+1 more)

### Community 1 - "acquisition.c"
Cohesion: 0.09
Nodes (14): vib_header_t, vib_status_payload_t, build_table(), vib_crc16(), acquisition_start(), acquisition_task(), send_block(), setup_int1() (+6 more)

### Community 2 - "SimulatedSource"
Cohesion: 0.07
Nodes (16): _feed(), _data_pkt(), test_crc_little_endian_at_end(), test_crc_vector_matches_firmware(), test_resync_after_garbage_and_crc_error(), test_roundtrip_byte_by_byte(), test_status_payload(), LinkStats (+8 more)

### Community 3 - "test_sensors_baseline.py"
Cohesion: 0.15
Nodes (11): test_node_gap_detection_and_analysis(), _baseline_and_node(), feed(), test_baseline_alarm_and_save_load(), test_baseline_detects_new_fault(), test_each_sensor_end_to_end(), test_single_window_compare_still_works(), _track() (+3 more)

### Community 4 - "Guia rápido"
Cohesion: 0.08
Nodes (22): Diagrama de pinagem — ESP32 ↔ acelerômetro (LSM6DS3, BMI160 ou ICM-20948), Dicas de montagem (importantes para medir vibração de verdade), Nomes dos pinos em cada módulo, Protocolo serial ESP32 → PC (versão 1), 0. O que você precisa, 1. Instalar o ESP-IDF v6 (uma vez só), 2. Compilar e gravar o firmware, 3. Instalar o software do PC (+14 more)

### Community 5 - "main"
Cohesion: 0.09
Nodes (10): test_record_and_export(), test_baseline_from_recording(), run_gui(), AcquisitionHub, guess_esp32_ports(), list_serial_ports(), main(), Packet (+2 more)

### Community 6 - "vib_sensor_t"
Cohesion: 0.12
Nodes (26): graphify, vibration-detector — contexto para assistentes de IA, configure(), fifo_read(), range_code(), wr(), bank(), configure() (+18 more)

### Community 7 - "numpy"
Cohesion: 0.09
Nodes (21): test_impulsive_signal_raises_kurtosis(), test_metrics_sine_and_gaussian(), test_sine_amplitude_frequency_and_band_rms(), test_short_capture_raises(), test_third_octave_bands(), band_db(), build_node(), compare() (+13 more)

### Community 8 - "vib_sensor"
Cohesion: 0.15
Nodes (12): vib_sensor, bandwidth_hz, chip_id, drv, fifo_capacity_samples, full_scale_g, odr_hz, poll_ms (+4 more)

### Community 11 - "MainWindow"
Cohesion: 0.09
Nodes (4): Baseline, build_from_session(), MainWindow, Spectrogram

## Knowledge Gaps
- **11 isolated node(s):** `drv`, `spi`, `type`, `chip_id`, `odr_hz` (+6 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 115 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **3 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `MainWindow` connect `MainWindow` to `gui.py`, `main`?**
  _High betweenness centrality (0.045) - this node is a cross-community bridge._
- **Are the 3 inferred relationships involving `NodeState` (e.g. with `analyze()` and `AcquisitionHub`) actually correct?**
  _`NodeState` has 3 INFERRED edges - model-reasoned connections that need verification._
- **What connects `drv`, `spi`, `type` to the rest of the system?**
  _11 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `gui.py` be split into smaller, more focused modules?**
  _Cohesion score 0.10887096774193548 - nodes in this community are weakly interconnected._
- **Why does `NodeState` connect `test_sensors_baseline.py` to `gui.py`, `SimulatedSource`, `main`?**
  _High betweenness centrality (0.037) - this node is a cross-community bridge._
- **Are the 4 inferred relationships involving `MainWindow` (e.g. with `AlarmChecker` and `AlarmEvent`) actually correct?**
  _`MainWindow` has 4 INFERRED edges - model-reasoned connections that need verification._
- **Should `acquisition.c` be split into smaller, more focused modules?**
  _Cohesion score 0.0873015873015873 - nodes in this community are weakly interconnected._