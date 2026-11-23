# Graph Report - vibration-detector  (2026-11-23)

## Corpus Check
- 30 files · ~12,317 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 5 file(s) not represented in the graph (top: (none) 3, .projbuild 1, .defaults 1)

## Summary
- 251 nodes · 545 edges · 13 communities (8 shown, 5 thin omitted)
- Extraction: 91% EXTRACTED · 9% INFERRED · 0% AMBIGUOUS · INFERRED: 49 edges (avg confidence: 0.88)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `1afc4751`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- gui.py
- acquisition.c
- StreamParser
- __main__.py
- Guia rápido
- main
- lsm6ds3.c
- numpy
- Packet
- vibration-detector — contexto para assistentes de IA
- MainWindow

## God Nodes (most connected - your core abstractions)
1. `main()` - 20 edges
2. `NodeState` - 18 edges
3. `StreamParser` - 17 edges
4. `AcquisitionHub` - 17 edges
5. `Packet` - 17 edges
6. `analyze()` - 16 edges
7. `Recorder` - 15 edges
8. `AlarmChecker` - 14 edges
9. `SimulatedSource` - 14 edges
10. `test_record_and_export()` - 12 edges

## Surprising Connections (you probably didn't know these)
- `acquisition_start()` --calls--> `lsm6ds3_init()`  [INFERRED]
  firmware/main/acquisition.c → firmware/components/lsm6ds3/lsm6ds3.c
- `AcquisitionHub` --uses--> `NodeState`  [INFERRED]
  pc/vibmon/hub.py → pc/vibmon/node.py
- `NodeState` --uses--> `Packet`  [INFERRED]
  pc/vibmon/node.py → pc/vibmon/protocol.py
- `SimulatedSource` --uses--> `StreamParser`  [INFERRED]
  pc/vibmon/sources.py → pc/vibmon/protocol.py
- `AcquisitionHub` --uses--> `Packet`  [INFERRED]
  pc/vibmon/hub.py → pc/vibmon/protocol.py

## Import Cycles
- None detected.

## Communities (13 total, 5 thin omitted)

### Community 0 - "gui.py"
Cohesion: 0.09
Nodes (12): test_alarm_hold_and_hook(), test_example_thresholds_file_loads(), test_node_gap_detection_and_analysis(), AlarmChecker, AlarmEvent, Threshold, _value(), analyze() (+4 more)

### Community 1 - "acquisition.c"
Cohesion: 0.10
Nodes (12): vib_header_t, vib_status_payload_t, build_table(), vib_crc16(), acquisition_start(), setup_int1(), app_main(), node_id_detect() (+4 more)

### Community 2 - "StreamParser"
Cohesion: 0.13
Nodes (11): _feed(), _data_pkt(), test_crc_little_endian_at_end(), test_crc_vector_matches_firmware(), test_resync_after_garbage_and_crc_error(), test_roundtrip_byte_by_byte(), test_status_payload(), crc16() (+3 more)

### Community 3 - "__main__.py"
Cohesion: 0.13
Nodes (3): guess_esp32_ports(), list_serial_ports(), LinkStats

### Community 4 - "Guia rápido"
Cohesion: 0.09
Nodes (19): Diagrama de pinagem — ESP32 ↔ LSM6DS3, Dicas de montagem (importantes para medir vibração de verdade), Protocolo serial ESP32 → PC (versão 1), 0. O que você precisa, 1. Instalar o ESP-IDF v6 (uma vez só), 2. Compilar e gravar o firmware, 3. Instalar o software do PC, 4. Rodar (+11 more)

### Community 5 - "main"
Cohesion: 0.14
Nodes (7): test_record_and_export(), run_gui(), AcquisitionHub, main(), read_vib(), Recorder, SimulatedSource

### Community 6 - "lsm6ds3.c"
Cohesion: 0.23
Nodes (12): lsm6ds3_config_t, lsm6ds3_fifo_status_t, lsm6ds3_fs_t, lsm6ds3_t, fs_code(), lsm6ds3_fifo_read_words(), lsm6ds3_fifo_restart(), lsm6ds3_fifo_status() (+4 more)

### Community 7 - "numpy"
Cohesion: 0.15
Nodes (12): test_impulsive_signal_raises_kurtosis(), test_metrics_sine_and_gaussian(), test_sine_amplitude_frequency_and_band_rms(), band_rms(), dominant_frequency(), make_window(), metrics(), Metrics (+4 more)

## Knowledge Gaps
- **5 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Packet` connect `Packet` to `gui.py`, `StreamParser`, `__main__.py`, `main`?**
  _High betweenness centrality (0.051) - this node is a cross-community bridge._
- **Are the 8 inferred relationships involving `main()` (e.g. with `.add_serial()` and `.add_simulated()`) actually correct?**
  _`main()` has 8 INFERRED edges - model-reasoned connections that need verification._
- **Should `gui.py` be split into smaller, more focused modules?**
  _Cohesion score 0.08585858585858586 - nodes in this community are weakly interconnected._
- **Why does `NodeState` connect `gui.py` to `Packet`, `__main__.py`, `main`?**
  _High betweenness centrality (0.044) - this node is a cross-community bridge._
- **Are the 3 inferred relationships involving `NodeState` (e.g. with `analyze()` and `AcquisitionHub`) actually correct?**
  _`NodeState` has 3 INFERRED edges - model-reasoned connections that need verification._
- **Should `acquisition.c` be split into smaller, more focused modules?**
  _Cohesion score 0.0957983193277311 - nodes in this community are weakly interconnected._
- **Why does `analyze()` connect `gui.py` to `__main__.py`, `MainWindow`, `main`, `numpy`?**
  _High betweenness centrality (0.040) - this node is a cross-community bridge._