"""Interface grafica em tempo real (pyqtgraph + Qt).

Layout:
  [barra: Gravar | rotulo | RPM | velocidade | nota | Anotar | janela FFT | tipo | eixo FFT | unidade | log]
  [Mancal 1 — tempo X/Y/Z ] [indicadores Mancal 1]
  [Mancal 2 — tempo X/Y/Z ] [indicadores Mancal 2]
  [Espectro FFT: Mancal 1 x Mancal 2]
"""
from __future__ import annotations

import time

import numpy as np
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

from .alarms import AlarmChecker, AlarmEvent
from .analysis import AXES, NodeAnalysis, analyze
from .hub import AcquisitionHub
from .recorder import Recorder

# Paleta (tema escuro) — cores categoricas fixas por entidade, nunca por ordem
SURFACE = "#1a1a19"
INK = "#f0efec"
INK2 = "#c3c2b7"
GRID_ALPHA = 0.15
AXIS_COLORS = {"x": "#3987e5", "y": "#d95926", "z": "#199e70"}
NODE_COLORS = {1: "#3987e5", 2: "#d95926"}
STATUS = {"good": "#0ca30c", "warning": "#fab219", "serious": "#ec835a", "critical": "#d03b3b"}

TIME_SPAN_S = 0.5      # janela visivel do grafico no tempo
TIME_FPS = 25
FFT_HZ = 5


class NodePanel(QtWidgets.QGroupBox):
    """Indicadores numericos de um mancal."""

    ROWS = [("rms", "RMS"), ("p2p", "Pico a pico"), ("kurt", "Curtose"), ("dom", "Freq. dominante"),
            ("dom_amp", "Amplitude dom.")]

    def __init__(self, title: str, color: str):
        super().__init__(title)
        self.setStyleSheet(f"QGroupBox {{ color: {INK}; font-weight: bold; border: 2px solid {color};"
                           f" border-radius: 6px; margin-top: 10px; padding: 6px; }}"
                           f"QGroupBox::title {{ subcontrol-origin: margin; left: 8px; }}"
                           f"QLabel {{ color: {INK}; font-weight: normal; }}")
        g = QtWidgets.QGridLayout(self)
        g.setVerticalSpacing(2)
        self.cells: dict[tuple[str, str], QtWidgets.QLabel] = {}
        g.addWidget(QtWidgets.QLabel(""), 0, 0)
        for j, ax in enumerate(AXES):
            h = QtWidgets.QLabel(f"<b>{ax.upper()}</b> <span style='color:{AXIS_COLORS[ax]}'>━</span>")
            h.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
            g.addWidget(h, 0, j + 1)
        for i, (key, label) in enumerate(self.ROWS, start=1):
            g.addWidget(QtWidgets.QLabel(label), i, 0)
            for j, ax in enumerate(AXES):
                c = QtWidgets.QLabel("—")
                c.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
                c.setFont(QtGui.QFont("monospace"))
                g.addWidget(c, i, j + 1)
                self.cells[(key, ax)] = c
        r = len(self.ROWS) + 1
        self.overall = QtWidgets.QLabel("RMS global: —")
        self.overall.setStyleSheet(f"color: {INK}; font-size: 15px; font-weight: bold;")
        g.addWidget(self.overall, r, 0, 1, 4)
        self.status = QtWidgets.QLabel("aguardando dados…")
        self.status.setWordWrap(True)
        g.addWidget(self.status, r + 1, 0, 1, 4)
        self.link = QtWidgets.QLabel("")
        self.link.setStyleSheet(f"color: {INK2}; font-size: 11px;")
        self.link.setWordWrap(True)
        g.addWidget(self.link, r + 2, 0, 1, 4)

    def show_analysis(self, res: NodeAnalysis) -> None:
        u = res.unit
        for ax, ar in res.axes.items():
            m = ar.metrics
            self.cells[("rms", ax)].setText(f"{m.rms:.4f} {u}")
            self.cells[("p2p", ax)].setText(f"{m.peak_to_peak:.4f} {u}")
            self.cells[("kurt", ax)].setText(f"{m.kurtosis:.2f}")
            self.cells[("dom", ax)].setText(f"{ar.dom_freq:.1f} Hz")
            self.cells[("dom_amp", ax)].setText(f"{ar.dom_amp:.4f} {u}")
        self.overall.setText(f"RMS global: {res.overall_rms:.4f} {u}")

    def set_status(self, text: str, level: str) -> None:
        icon = {"good": "●", "warning": "▲", "serious": "▲", "critical": "■"}[level]
        self.status.setText(f"<span style='color:{STATUS[level]}'>{icon}</span> {text}")


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, hub: AcquisitionHub, recorder: Recorder, alarms: AlarmChecker,
                 n_window: int = 2048, window: str = "hann"):
        super().__init__()
        self.hub, self.rec, self.alarms = hub, recorder, alarms
        self.setWindowTitle("vibmon — Mancais do túnel de vento")
        self.resize(1500, 950)
        pg.setConfigOptions(antialias=False, background=SURFACE, foreground=INK2)
        self._last_beep = 0.0
        self._results: dict[int, NodeAnalysis] = {}
        alarms.hooks.append(self._on_alarm)

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)
        root.addLayout(self._build_toolbar(n_window, window))

        grid = QtWidgets.QGridLayout()
        root.addLayout(grid, 1)
        self.time_plots, self.time_curves, self.panels = {}, {}, {}
        for row, nid in enumerate((1, 2)):
            p = pg.PlotWidget(title=f"<span style='color:{INK}'>Mancal {nid} — aceleração no tempo</span>")
            p.showGrid(x=True, y=True, alpha=GRID_ALPHA)
            p.setLabel("bottom", "tempo", "s")
            p.setClipToView(True)
            p.setDownsampling(auto=True, mode="peak")
            leg = p.addLegend(offset=(10, 5), colCount=3)
            leg.setLabelTextColor(INK)
            self.time_curves[nid] = {ax: p.plot(pen=pg.mkPen(AXIS_COLORS[ax], width=1.5), name=ax.upper())
                                     for ax in AXES}
            self.time_plots[nid] = p
            grid.addWidget(p, row, 0)
            panel = NodePanel(f"Mancal {nid}", NODE_COLORS[nid])
            panel.setFixedWidth(430)
            self.panels[nid] = panel
            grid.addWidget(panel, row, 1)

        self.fft_plot = pg.PlotWidget(title=f"<span style='color:{INK}'>Espectro de frequência (FFT)</span>")
        self.fft_plot.showGrid(x=True, y=True, alpha=GRID_ALPHA)
        self.fft_plot.setLabel("bottom", "frequência", "Hz")
        leg = self.fft_plot.addLegend(offset=(-10, 5))
        leg.setLabelTextColor(INK)
        self.fft_curves = {nid: self.fft_plot.plot(pen=pg.mkPen(NODE_COLORS[nid], width=1.5), name=f"Mancal {nid}")
                           for nid in (1, 2)}
        self.fft_legend = leg
        # PlotDataItem (e nao ScatterPlotItem) para respeitar o modo log do eixo Y
        self.fft_peaks = {nid: pg.PlotDataItem(pen=None, symbol="o", symbolSize=9,
                                               symbolPen=pg.mkPen(SURFACE, width=2),
                                               symbolBrush=NODE_COLORS[nid]) for nid in (1, 2)}
        for sp in self.fft_peaks.values():
            self.fft_plot.addItem(sp)
        grid.addWidget(self.fft_plot, 2, 0, 1, 2)
        grid.setRowStretch(0, 3)
        grid.setRowStretch(1, 3)
        grid.setRowStretch(2, 4)

        self.statusBar().setStyleSheet(f"color: {INK2};")
        self.setStyleSheet(f"QMainWindow, QWidget {{ background: #0d0d0d; color: {INK}; }}"
                           f"QLineEdit, QComboBox, QSpinBox {{ background: {SURFACE}; border: 1px solid #383835;"
                           f" padding: 3px; }} QPushButton {{ background: #383835; padding: 5px 12px;"
                           f" border-radius: 4px; }}")

        self._t_timer = QtCore.QTimer(self, timeout=self._update_time, interval=int(1000 / TIME_FPS))
        self._f_timer = QtCore.QTimer(self, timeout=self._update_fft, interval=int(1000 / FFT_HZ))
        self._t_timer.start()
        self._f_timer.start()

    # ------------------------------------------------------------------ toolbar
    def _build_toolbar(self, n_window: int, window: str) -> QtWidgets.QHBoxLayout:
        bar = QtWidgets.QHBoxLayout()
        self.btn_rec = QtWidgets.QPushButton("● Gravar")
        self.btn_rec.clicked.connect(self.toggle_record)
        self.ed_label = QtWidgets.QLineEdit(placeholderText="rótulo do ensaio")
        self.ed_rpm = QtWidgets.QLineEdit(placeholderText="RPM")
        self.ed_rpm.setFixedWidth(70)
        self.ed_speed = QtWidgets.QLineEdit(placeholderText="vel. m/s")
        self.ed_speed.setFixedWidth(70)
        self.ed_note = QtWidgets.QLineEdit(placeholderText="observação")
        btn_ann = QtWidgets.QPushButton("Anotar")
        btn_ann.clicked.connect(self.annotate)
        self.ed_note.returnPressed.connect(self.annotate)
        self.cb_n = QtWidgets.QComboBox()
        self.cb_n.addItems(["1024", "2048", "4096", "8192"])
        self.cb_n.setCurrentText(str(n_window))
        self.cb_win = QtWidgets.QComboBox()
        self.cb_win.addItems(["hann", "hamming", "rect"])
        self.cb_win.setCurrentText(window)
        self.cb_axis = QtWidgets.QComboBox()
        self.cb_axis.addItems(["X", "Y", "Z", "XYZ (soma)"])
        self.cb_unit = QtWidgets.QComboBox()
        self.cb_unit.addItems(["g", "m/s²"])
        self.ck_dc = QtWidgets.QCheckBox("Remover DC")
        self.ck_dc.setToolTip("Remove a média (gravidade/offset) do gráfico no tempo. A FFT e as métricas sempre removem.")
        self.ck_dc.setChecked(True)
        self.ck_log = QtWidgets.QCheckBox("FFT log")
        self.ck_log.setChecked(True)
        self.ck_log.toggled.connect(lambda v: self.fft_plot.setLogMode(y=v))
        for w in (self.btn_rec, self.ed_label, QtWidgets.QLabel("  RPM:"), self.ed_rpm, self.ed_speed,
                  self.ed_note, btn_ann, QtWidgets.QLabel("  Janela FFT:"), self.cb_n, self.cb_win,
                  QtWidgets.QLabel("Eixo:"), self.cb_axis, QtWidgets.QLabel("Unidade:"), self.cb_unit, self.ck_dc, self.ck_log):
            bar.addWidget(w)
        return bar

    # ------------------------------------------------------------------ acoes
    def toggle_record(self) -> None:
        if self.rec.recording:
            d = self.rec.stop()
            self.btn_rec.setText("● Gravar")
            self.btn_rec.setStyleSheet("")
            self.statusBar().showMessage(f"Gravação salva em {d}", 10000)
        else:
            d = self.rec.start(self.ed_label.text(), {"ports": dict(self.hub.port_map)})
            self.annotate(auto_note="início da gravação")
            self.btn_rec.setText("■ Parar")
            self.btn_rec.setStyleSheet(f"background: {STATUS['critical']}; color: white;")
            self.statusBar().showMessage(f"Gravando em {d}")

    def annotate(self, auto_note: str | None = None) -> None:
        if not self.rec.recording:
            self.statusBar().showMessage("Inicie a gravação para registrar anotações", 4000)
            return
        note = auto_note if isinstance(auto_note, str) else self.ed_note.text()
        idx = {nid: n.total_samples for nid, n in self.hub.nodes.items()}
        self.rec.annotate(self.ed_rpm.text(), self.ed_speed.text(), note, idx)
        if not isinstance(auto_note, str):
            self.ed_note.clear()
        self.statusBar().showMessage(f"Anotado: RPM={self.ed_rpm.text()} {note}", 4000)

    def _on_alarm(self, ev: AlarmEvent) -> None:
        self.statusBar().showMessage(str(ev), 8000)
        if ev.rule.level == "alarm" and time.time() - self._last_beep > 2:
            QtWidgets.QApplication.beep()
            self._last_beep = time.time()

    # ------------------------------------------------------------------ atualizacao
    def _update_time(self) -> None:
        unit = self.cb_unit.currentText()
        k = 9.80665 if unit == "m/s²" else 1.0
        for nid, node in self.hub.nodes.items():
            fs = node.fs_nominal
            n = int(TIME_SPAN_S * fs)
            d = node.latest(n)
            if d.shape[0] == 0:
                continue
            t = (np.arange(d.shape[0]) - d.shape[0]) / fs
            if self.ck_dc.isChecked():
                d = d - d.mean(axis=0)
            for i, ax in enumerate(AXES):
                self.time_curves[nid][ax].setData(t, d[:, i] * k)
            self.time_plots[nid].setLabel("left", "aceleração", unit)

    def _update_fft(self) -> None:
        self.hub.refresh_crc_stats()
        n = int(self.cb_n.currentText())
        win = self.cb_win.currentText()
        unit = self.cb_unit.currentText()
        axis_sel = self.cb_axis.currentText()
        self.fft_plot.setLogMode(y=self.ck_log.isChecked())
        self.fft_plot.setLabel("left", "amplitude (pico)", unit)
        for nid, node in self.hub.nodes.items():
            panel = self.panels[nid]
            st = node.stats
            info = st.status
            panel.link.setText(
                f"porta {st.port or '—'} · fs≈{node.fs_estimate():.0f} Hz · ±{node.full_scale_g} g · "
                f"pacotes {st.packets} · CRC {st.crc_errors} · perdidas {st.lost_samples} · "
                f"overrun {info.get('fifo_overruns', 0)} · drops {info.get('block_drops', 0)}")
            if not node.online():
                panel.set_status("sem dados (cabo USB / porta?)", "serious")
                self.fft_curves[nid].setData([], [])
                self.fft_peaks[nid].setData([], [])
                continue
            if info and info.get("who_am_i") == 0:
                panel.set_status("ESP32 conectado, mas o sensor LSM6DS3 NÃO respondeu (fiação SPI)", "critical")
                continue
            res = analyze(node, n, win, unit)
            if res is None:
                panel.set_status("acumulando amostras…", "warning")
                continue
            self._results[nid] = res
            panel.show_analysis(res)
            self.alarms.check(res)
            active = self.alarms.active_for(nid)
            if active:
                worst = "critical" if any(e.rule.level == "alarm" for e in active) else "warning"
                panel.set_status("; ".join(f"{e.rule.name} ({e.axis})" for e in active), worst)
            else:
                panel.set_status("OK" if self.alarms.rules else "OK (limiares ainda não definidos)", "good")

            if axis_sel.startswith("XYZ"):
                amp = np.sqrt(sum(res.axes[a].spectrum.amp ** 2 for a in AXES))
                freqs = res.axes["x"].spectrum.freqs
                f_dom = freqs[1 + int(np.argmax(amp[1:]))]
                a_dom = amp.max()
            else:
                ar = res.axes[axis_sel.lower()]
                freqs, amp, f_dom, a_dom = ar.spectrum.freqs, ar.spectrum.amp, ar.dom_freq, ar.dom_amp
            amp = np.maximum(amp, 1e-7)
            self.fft_curves[nid].setData(freqs[1:], amp[1:])
            self.fft_peaks[nid].setData([f_dom], [max(a_dom, 1e-7)])
            lbl = self.fft_legend.getLabel(self.fft_curves[nid])
            if lbl is not None:
                lbl.setText(f"Mancal {nid} — pico {f_dom:.1f} Hz ({a_dom:.3g} {unit})")

        rec = f" · ● GRAVANDO {self.rec.bytes_written / 1e6:.1f} MB" if self.rec.recording else ""
        warn = f" · {self.hub.warnings[-1]}" if self.hub.warnings else ""
        if not self.statusBar().currentMessage() or rec:
            self.statusBar().showMessage(f"portas: {self.hub.port_map}{rec}{warn}")

    def closeEvent(self, ev) -> None:
        if self.rec.recording:
            self.rec.stop()
        self.hub.stop()
        super().closeEvent(ev)


def run_gui(hub: AcquisitionHub, recorder: Recorder, alarms: AlarmChecker, **kw) -> int:
    app = pg.mkQApp("vibmon")
    w = MainWindow(hub, recorder, alarms, **kw)
    w.show()
    return app.exec()
