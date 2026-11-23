# vibration-detector — contexto para assistentes de IA

Monitoramento de vibracao dos 2 mancais do motor do tunel de vento.
- `firmware/` — ESP-IDF v6 (C). 1 binario para os 2 nos; ID do mancal pelo GPIO27 (jumper GND = MANCAL_2).
  `components/lsm6ds3` (driver SPI + FIFO), `components/vib_protocol` (cabecalho + CRC16),
  `main/acquisition.c` (Core 1, INT1 -> burst read), `main/usb_link.c` (Core 0, UART0 921600).
- `pc/vibmon/` — Python: `protocol.py` (espelho exato do protocolo C), `sources.py` (serial + simulador),
  `hub.py`, `node.py`, `dsp.py`, `analysis.py`, `alarms.py`, `recorder.py`, `export.py`, `gui.py` (pyqtgraph).
- Testes: `cd pc && python -m pytest -q`. Rodar sem hardware: `python -m vibmon --simulate`.
- Qualquer mudanca no protocolo deve ser feita nos DOIS lados (vib_protocol.h e protocol.py) e no teste de CRC.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- When graphify-out/graph.json exists, run `graphify query "<question>"` before your first grep, search or source-file read for a codebase question, and put this rule in every subagent prompt that explores code. Read raw files only for lines the graph or an earlier read already located. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. When the answer is under a Markdown heading, `graphify section "<heading>"` prints that section's text, so the .md need not be opened. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
- A committed graphify-out/graph.json is rewritten whole on every build, so a large diff to it in a pull request is expected. Do not flag it as churn or suggest skipping the graph rebuild or its hooks.
