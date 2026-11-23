"""Ponto de entrada:  python -m vibmon [opcoes]

Exemplos:
    python -m vibmon --list-ports
    python -m vibmon --simulate                     # sem hardware
    python -m vibmon COM3 COM4                      # Windows
    python -m vibmon /dev/ttyUSB0 /dev/ttyUSB1      # Linux
    python -m vibmon                                # tenta detectar as portas sozinho
    python -m vibmon --headless --record ensaio1    # sem janela: imprime metricas e grava
"""
from __future__ import annotations

import argparse
import logging
import sys
import time

from .alarms import AlarmChecker
from .analysis import analyze
from .hub import AcquisitionHub, guess_esp32_ports, list_serial_ports
from .recorder import Recorder


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="vibmon", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ports", nargs="*", help="portas seriais (a ordem nao importa: o ID vem do ESP32)")
    ap.add_argument("--baud", type=int, default=921600, help="deve ser igual ao do firmware (menuconfig)")
    ap.add_argument("--simulate", action="store_true", help="usa dois mancais simulados")
    ap.add_argument("--sim-rpm", type=float, default=1770.0)
    ap.add_argument("--window", type=int, default=2048, help="amostras por janela de FFT")
    ap.add_argument("--window-type", default="hann", choices=("hann", "hamming", "rect"))
    ap.add_argument("--thresholds", help="arquivo JSON de limiares (ver config/thresholds.example.json)")
    ap.add_argument("--record-dir", default="recordings")
    ap.add_argument("--list-ports", action="store_true")
    ap.add_argument("--headless", action="store_true", help="sem GUI: imprime metricas no terminal")
    ap.add_argument("--record", metavar="ROTULO", help="(headless) grava a sessao com este rotulo")
    ap.add_argument("--duration", type=float, default=0, help="(headless) segundos; 0 = ate Ctrl+C")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format="%(levelname)s %(message)s")

    if a.list_ports:
        for dev, desc in list_serial_ports():
            print(f"{dev:20s} {desc}")
        return 0

    hub = AcquisitionHub()
    if a.simulate:
        hub.add_simulated(1, rpm=a.sim_rpm)
        hub.add_simulated(2, rpm=a.sim_rpm)
    else:
        ports = a.ports or guess_esp32_ports()
        if not ports:
            print("Nenhuma porta encontrada. Use --list-ports e informe as portas, ou --simulate.")
            return 1
        logging.info("abrindo portas: %s", ", ".join(ports))
        for p in ports:
            hub.add_serial(p, a.baud)

    rec = Recorder(a.record_dir)
    hub.packet_listeners.append(rec.on_packet)
    alarms = AlarmChecker.from_file(a.thresholds) if a.thresholds else AlarmChecker()
    alarms.hooks.append(lambda ev: logging.warning("%s", ev))
    hub.start()

    if not a.headless:
        from .gui import run_gui

        return run_gui(hub, rec, alarms, n_window=a.window, window=a.window_type)

    if a.record:
        print("gravando em", rec.start(a.record, {"ports": a.ports}))
    t0 = time.monotonic()
    try:
        while not a.duration or time.monotonic() - t0 < a.duration:
            time.sleep(1.0)
            hub.refresh_crc_stats()
            for nid, node in hub.nodes.items():
                res = analyze(node, a.window, a.window_type)
                st = node.stats
                if res is None:
                    print(f"M{nid}: sem dados suficientes (porta={st.port or '-'})")
                    continue
                alarms.check(res)
                ax = " ".join(f"{k.upper()}: rms={v.metrics.rms:.4f}g kurt={v.metrics.kurtosis:.2f} "
                              f"f={v.dom_freq:.1f}Hz" for k, v in res.axes.items())
                print(f"M{nid} [{st.port}] fs={res.fs:.0f}Hz {ax} | pkts={st.packets} crc={st.crc_errors} "
                      f"perdidas={st.lost_samples}")
    except KeyboardInterrupt:
        pass
    finally:
        if rec.recording:
            print("salvo em", rec.stop())
        hub.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
