"""Interface grafica em tempo real (pyqtgraph + Qt).

Layout:
  [barra 1: Gravar | rotulo | RPM | velocidade | nota | Anotar | janela FFT | tipo | eixo | unidade | DC | log]
  [barra 2: Baseline: nome | Carregar... | Capturar N s | mostrar na FFT]
  [Mancal 1 — tempo X/Y/Z ] [indicadores Mancal 1]
  [Mancal 2 — tempo X/Y/Z ] [indicadores Mancal 2]
  [abas: Espectro (FFT) Mancal 1 x Mancal 2 + baseline | Espectrograma (cascata)]
"""
from __future__ import annotations

import time
from collections import deque
from datetime import datetime
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

from .alarms import AlarmChecker, AlarmEvent
from .analysis import AXES, NodeAnalysis, analyze
from .baseline import Baseline, build_node
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
# Rampa sequencial de um so matiz (azul), escuro -> claro, para o espectrograma
SPECTRO_RAMP = ["#1a1a19", "#0d366b", "#1c5cab", "#3987e5", "#86b6ef", "#cde2fb"]

TIME_SPAN_S = 0.5      # janela visivel do grafico no tempo
TIME_FPS = 25
FFT_HZ = 5
SPECTRO_SECONDS = 60
BASELINE_DIR = Path("baselines")


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
        self.sensor = QtWidgets.QLabel("sensor: —")
        self.sensor.setStyleSheet(f"color: {INK2};")
        g.addWidget(self.sensor, 0, 0, 1, 4)
        self.cells: dict[tuple[str, str], QtWidgets.QLabel] = {}
        for j, ax in enumerate(AXES):
            h = QtWidgets.QLabel(f"<b>{ax.upper()}</b> <span style='color:{AXIS_COLORS[ax]}'>━</span>")
            h.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
            g.addWidget(h, 1, j + 1)
        for i, (key, label) in enumerate(self.ROWS, start=2):
            g.addWidget(QtWidgets.QLabel(label), i, 0)
            for j, ax in enumerate(AXES):
                c = QtWidgets.QLabel("—")
                c.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
                c.setFont(QtGui.QFont("monospace"))
                g.addWidget(c, i, j + 1)
                self.cells[(key, ax)] = c
        r = len(self.ROWS) + 2
        self.overall = QtWidgets.QLabel("RMS global: —")
        self.overall.setStyleSheet(f"color: {INK}; font-size: 15px; font-weight: bold;")
        g.addWidget(self.overall, r, 0, 1, 4)
        self.baseline = QtWidgets.QLabel("baseline: —")
        g.addWidget(self.baseline, r + 1, 0, 1, 4)
        self.status = QtWidgets.QLabel("aguardando dados…")
        self.status.setWordWrap(True)
        g.addWidget(self.status, r + 2, 0, 1, 4)
        self.link = QtWidgets.QLabel("")
        self.link.setStyleSheet(f"color: {INK2}; font-size: 11px;")
        self.link.setWordWrap(True)
        g.addWidget(self.link, r + 3, 0, 1, 4)

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


class Spectrogram:
    """Cascata (tempo x frequencia) de um mancal, em dB."""

    def __init__(self, plot: pg.PlotItem, title: str):
        self.plot = plot
        plot.setTitle(f"<span style='color:{INK}'>{title}</span>")
        plot.setLabel("bottom", "tempo", "s")
        plot.setLabel("left", "frequência", "Hz")
        self.img = pg.ImageItem(axisOrder="row-major")
        plot.addItem(self.img)
        cmap = pg.ColorMap(np.linspace(0, 1, len(SPECTRO_RAMP)), SPECTRO_RAMP)
        self.img.setColorMap(cmap)
        self.bar = pg.ColorBarItem(colorMap=cmap, label="dB (re 1 g)", interactive=False, width=12)
        self.bar.setImageItem(self.img, insert_in=plot)
        self.rows: deque[np.ndarray] = deque(maxlen=SPECTRO_SECONDS * FFT_HZ)
        self.stamps: deque[float] = deque(maxlen=SPECTRO_SECONDS * FFT_HZ)
        self.f_max = 0.0

    def push(self, freqs: np.ndarray, amp: np.ndarray) -> None:
        # recomeca se mudou a janela FFT ou a taxa (a estimativa de fs oscila
        # alguns Hz o tempo todo; so uma mudanca > 2 % conta)
        if self.rows and (self.rows[-1].size != amp.size or abs(freqs[-1] - self.f_max) > 0.02 * self.f_max):
            self.rows.clear()
            self.stamps.clear()
        self.f_max = float(freqs[-1])
        self.rows.append((20 * np.log10(np.maximum(amp, 1e-7))).astype(np.float32))
        self.stamps.append(time.monotonic())

    def redraw(self) -> None:
        if len(self.rows) < 2:
            return
        img = np.stack(self.rows, axis=1)  # (freq, tempo)
        lo, hi = np.percentile(img, [50, 99.9])  # ruido escuro, tons em destaque
        self.img.setImage(img, autoLevels=False, levels=(lo, hi))
        self.bar.setLevels((lo, hi))
        span = self.stamps[-1] - self.stamps[0]
        self.img.setRect(QtCore.QRectF(-span, 0, span, self.f_max))

    def clear(self) -> None:
        self.rows.clear()
        self.stamps.clear()
        self.img.clear()


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, hub: AcquisitionHub, recorder: Recorder, alarms: AlarmChecker,
                 n_window: int = 2048, window: str = "hann"):
        super().__init__()
        self.hub, self.rec, self.alarms = hub, recorder, alarms
        self.setWindowTitle("vibmon — Mancais do túnel de vento")
        self.resize(1500, 1000)
        pg.setConfigOptions(antialias=False, background=SURFACE, foreground=INK2)
        self._last_beep = 0.0
        self._results: dict[int, NodeAnalysis] = {}
        self._capture_end = 0.0
        alarms.hooks.append(self._on_alarm)

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)
        root.addLayout(self._build_toolbar(n_window, window))
        root.addLayout(self._build_baseline_bar())

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
            panel.setFixedWidth(440)
            self.panels[nid] = panel
            grid.addWidget(panel, row, 1)

        self.tabs = QtWidgets.QTabWidget()
        self.tabs.setStyleSheet(f"QTabBar::tab {{ background: #383835; color: {INK}; padding: 4px 14px; }}"
                                f"QTabBar::tab:selected {{ background: {SURFACE}; }}")
        grid.addWidget(self.tabs, 2, 0, 1, 2)
        grid.setRowStretch(0, 3)
        grid.setRowStretch(1, 3)
        grid.setRowStretch(2, 4)

        # --- aba FFT
        self.fft_plot = pg.PlotWidget(title=f"<span style='color:{INK}'>Espectro de frequência (FFT)</span>")
        self.fft_plot.showGrid(x=True, y=True, alpha=GRID_ALPHA)
        self.fft_plot.setLabel("bottom", "frequência", "Hz")
        self.fft_legend = self.fft_plot.addLegend(offset=(-10, 5))
        self.fft_legend.setLabelTextColor(INK)
        self.fft_curves = {nid: self.fft_plot.plot(pen=pg.mkPen(NODE_COLORS[nid], width=1.5), name=f"Mancal {nid}")
                           for nid in (1, 2)}
        self.base_curves = {nid: self.fft_plot.plot(pen=pg.mkPen(NODE_COLORS[nid], width=1.2,
                                                                 style=QtCore.Qt.PenStyle.DashLine),
                                                    name=f"Baseline M{nid}") for nid in (1, 2)}
        # PlotDataItem (e nao ScatterPlotItem) para respeitar o modo log do eixo Y
        self.fft_peaks = {nid: pg.PlotDataItem(pen=None, symbol="o", symbolSize=9,
                                               symbolPen=pg.mkPen(SURFACE, width=2),
                                               symbolBrush=NODE_COLORS[nid]) for nid in (1, 2)}
        self.bw_lines = {}
        for nid in (1, 2):
            self.fft_plot.addItem(self.fft_peaks[nid])
            ln = pg.InfiniteLine(angle=90, movable=False,
                                 pen=pg.mkPen(NODE_COLORS[nid], width=1, style=QtCore.Qt.PenStyle.DotLine),
                                 label=f"banda M{nid}", labelOpts={"color": INK2, "position": 0.06 + 0.07 * nid})
            ln.setVisible(False)
            self.fft_plot.addItem(ln)
            self.bw_lines[nid] = ln
        self.tabs.addTab(self.fft_plot, "Espectro (FFT)")

        # --- aba espectrograma
        spec_w = pg.GraphicsLayoutWidget()
        self.spectro = {}
        for row, nid in enumerate((1, 2)):
            pi = spec_w.addPlot(row=row, col=0)
            self.spectro[nid] = Spectrogram(pi, f"Mancal {nid} — espectrograma (eixo selecionado)")
        self.tabs.addTab(spec_w, "Espectrograma")

        self.statusBar().setStyleSheet(f"color: {INK2};")
        self.setStyleSheet(f"QMainWindow, QWidget {{ background: #0d0d0d; color: {INK}; }}"
                           f"QLineEdit, QComboBox, QSpinBox {{ background: {SURFACE}; border: 1px solid #383835;"
                           f" padding: 3px; }} QPushButton {{ background: #383835; padding: 5px 12px;"
                           f" border-radius: 4px; }}")

        self._t_timer = QtCore.QTimer(self, timeout=self._update_time, interval=int(1000 / TIME_FPS))
        self._f_timer = QtCore.QTimer(self, timeout=self._update_fft, interval=int(1000 / FFT_HZ))
        self._t_timer.start()
        self._f_timer.start()
        self._refresh_baseline_label()

    # ------------------------------------------------------------------ barras
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
        self.cb_axis.currentTextChanged.connect(lambda _: [s.clear() for s in self.spectro.values()])
        self.cb_unit = QtWidgets.QComboBox()
        self.cb_unit.addItems(["g", "m/s²"])
        self.ck_dc = QtWidgets.QCheckBox("Remover DC")
        self.ck_dc.setToolTip("Remove a média (gravidade/offset) do gráfico no tempo. A FFT e as métricas sempre removem.")
        self.ck_dc.setChecked(True)
        self.ck_log = QtWidgets.QCheckBox("FFT log")
        self.ck_log.setChecked(True)
        for w in (self.btn_rec, self.ed_label, QtWidgets.QLabel("  RPM:"), self.ed_rpm, self.ed_speed,
                  self.ed_note, btn_ann, QtWidgets.QLabel("  Janela FFT:"), self.cb_n, self.cb_win,
                  QtWidgets.QLabel("Eixo:"), self.cb_axis, QtWidgets.QLabel("Unidade:"), self.cb_unit,
                  self.ck_dc, self.ck_log):
            bar.addWidget(w)
        return bar

    def _build_baseline_bar(self) -> QtWidgets.QHBoxLayout:
        bar = QtWidgets.QHBoxLayout()
        self.lb_base = QtWidgets.QLabel()
        btn_load = QtWidgets.QPushButton("Carregar baseline…")
        btn_load.clicked.connect(self.load_baseline_dialog)
        self.sp_capture = QtWidgets.QSpinBox(minimum=5, maximum=55, value=30, suffix=" s")
        self.btn_capture = QtWidgets.QPushButton("Capturar baseline")
        self.btn_capture.setToolTip("Grava a assinatura atual dos mancais como referência de motor saudável.\n"
                                    "Use com o motor em bom estado e rotação estável; o rótulo e o RPM\n"
                                    "da barra acima entram no nome do arquivo.")
        self.btn_capture.clicked.connect(self.start_capture)
        self.ck_base = QtWidgets.QCheckBox("Mostrar baseline na FFT")
        self.ck_base.setChecked(True)
        self.cb_range = QtWidgets.QComboBox()
        self.cb_range.addItems(["toda", "0–2 kHz", "0–1 kHz", "0–500 Hz", "0–200 Hz"])
        self.cb_range.setToolTip("Faixa mostrada na FFT e no espectrograma (o cálculo usa sempre a banda toda)")
        self.cb_range.currentTextChanged.connect(self._apply_freq_range)
        for w in (QtWidgets.QLabel("Baseline:"), self.lb_base, btn_load, self.sp_capture, self.btn_capture,
                  self.ck_base):
            bar.addWidget(w)
        bar.addStretch(1)
        bar.addWidget(QtWidgets.QLabel("Faixa de frequência:"))
        bar.addWidget(self.cb_range)
        return bar

    def _apply_freq_range(self, text: str | None = None) -> None:
        text = text or self.cb_range.currentText()
        limits = {"0–2 kHz": 2000, "0–1 kHz": 1000, "0–500 Hz": 500, "0–200 Hz": 200}
        f = limits.get(text)
        plots = [self.fft_plot.getPlotItem()] + [sp.plot for sp in self.spectro.values()]
        for i, pi in enumerate(plots):
            if f is None:
                pi.enableAutoRange(axis="x" if i == 0 else "y")
            elif i == 0:
                pi.setXRange(0, f, padding=0.01)
            else:
                pi.setYRange(0, f, padding=0.01)

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

    def set_baseline(self, b: Baseline | None) -> None:
        self.alarms.set_baseline(b)
        self._refresh_baseline_label()

    def _refresh_baseline_label(self) -> None:
        b = self.alarms.baseline
        if b is None:
            self.lb_base.setText(f"<span style='color:{INK2}'>nenhuma carregada</span>")
        else:
            nodes = ", ".join(f"M{k} {v.sensor} {v.fs:.0f} Hz" for k, v in sorted(b.nodes.items()))
            rpm = f" · {b.rpm} RPM" if b.rpm else ""
            self.lb_base.setText(f"<b>{b.label}</b>{rpm} · {nodes} · {b.created}")

    def load_baseline_dialog(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Carregar baseline", str(BASELINE_DIR),
                                                        "Baseline (*.json)")
        if path:
            try:
                self.set_baseline(Baseline.load(path))
                self.statusBar().showMessage(f"Baseline carregada: {path}", 6000)
            except Exception as exc:
                QtWidgets.QMessageBox.warning(self, "Baseline", f"Não foi possível carregar:\n{exc}")

    def start_capture(self) -> None:
        secs = self.sp_capture.value()
        online = [n for n in self.hub.nodes.values() if n.online()]
        if not online:
            self.statusBar().showMessage("Nenhum mancal enviando dados — nada para capturar", 6000)
            return
        self._capture_end = time.monotonic() + secs
        self.btn_capture.setEnabled(False)
        QtCore.QTimer.singleShot(int(secs * 1000), self._finish_capture)

    def _finish_capture(self) -> None:
        self.btn_capture.setEnabled(True)
        self._capture_end = 0.0
        secs = self.sp_capture.value()
        n_window = max(4096, int(self.cb_n.currentText()))
        nodes = {}
        for nid, node in self.hub.nodes.items():
            if not node.online():
                continue
            fs = node.fs_estimate()
            data = node.latest(int(secs * fs))
            try:
                nodes[nid] = build_node(data, fs, n_window, self.cb_win.currentText(), node.sensor_name)
            except ValueError as exc:
                self.statusBar().showMessage(f"Mancal {nid}: {exc}", 8000)
        if not nodes:
            return
        label = self.ed_label.text().strip() or "baseline"
        rpm = self.ed_rpm.text().strip()
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)
        name = f"{datetime.now():%Y%m%d_%H%M%S}_{safe}" + (f"_{rpm}rpm" if rpm else "")
        b = Baseline(label, nodes, rpm=rpm, source=f"captura ao vivo, {secs} s")
        path = b.save(BASELINE_DIR / f"{name}.json")
        self.set_baseline(b)
        self.statusBar().showMessage(f"Baseline salva e carregada: {path}", 10000)

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

    @staticmethod
    def _axis_amp(amps: dict[str, np.ndarray], axis_sel: str) -> np.ndarray:
        if axis_sel.startswith("XYZ"):
            return np.sqrt(sum(amps[a] ** 2 for a in AXES))
        return amps[axis_sel.lower()]

    def _baseline_limit(self) -> float:
        lims = [r.limit for r in self.alarms.rules if r.metric == "baseline_db" and r.enabled]
        return min(lims) if lims else 6.0

    def _update_fft(self) -> None:
        self.hub.refresh_crc_stats()
        n = int(self.cb_n.currentText())
        win = self.cb_win.currentText()
        unit = self.cb_unit.currentText()
        axis_sel = self.cb_axis.currentText()
        log = self.ck_log.isChecked()
        k_unit = 9.80665 if unit == "m/s²" else 1.0
        self.fft_plot.setLogMode(y=log)
        self.fft_plot.setLabel("left", "amplitude (pico)", unit)
        base = self.alarms.baseline
        for nid, node in self.hub.nodes.items():
            panel = self.panels[nid]
            st = node.stats
            info = st.status
            panel.sensor.setText(f"sensor: <b>{node.sensor_name}</b> · {node.fs_nominal:.0f} Hz · "
                                 f"banda {node.bandwidth_hz:.0f} Hz · ±{node.full_scale_g} g")
            panel.link.setText(
                f"porta {st.port or '—'} · fs≈{node.fs_estimate():.0f} Hz · "
                f"pacotes {st.packets} · CRC {st.crc_errors} · perdidas {st.lost_samples} · "
                f"overrun {info.get('fifo_overruns', 0)} · drops {info.get('block_drops', 0)}")
            nb = base.nodes.get(nid) if base else None
            show_base = nb is not None and self.ck_base.isChecked()
            if show_base:
                amp_b = self._axis_amp(nb.amp, axis_sel) * k_unit
                self.base_curves[nid].setData(nb.freqs[1:], np.maximum(amp_b[1:], 1e-7))
            else:
                self.base_curves[nid].setData([], [])

            if not node.online():
                panel.set_status("sem dados (cabo USB / porta?)", "serious")
                self.fft_curves[nid].setData([], [])
                self.fft_peaks[nid].setData([], [])
                self.bw_lines[nid].setVisible(False)
                continue
            if info and info.get("who_am_i") == 0:
                panel.set_status("ESP32 conectado, mas nenhum sensor respondeu (fiação SPI)", "critical")
                continue
            # marca a banda util do sensor quando ela fica abaixo de fs/2
            bw = node.bandwidth_hz
            self.bw_lines[nid].setValue(bw)
            self.bw_lines[nid].setVisible(bw < 0.95 * node.fs_nominal / 2)

            res = analyze(node, n, win, unit)
            if res is None:
                panel.set_status("acumulando amostras…", "warning")
                continue
            self._results[nid] = res
            panel.show_analysis(res)
            self.alarms.check(res)

            dev = self.alarms.last_deviation.get(nid) if nb is not None else None
            if dev:
                worst = max(dev.values(), key=lambda d: d.delta_db)
                lvl = "good" if worst.delta_db < self._baseline_limit() / 2 else (
                    "warning" if worst.delta_db < self._baseline_limit() else "serious")
                f = worst.band_hz
                f_txt = f"{f / 1000:.2f} kHz" if f >= 1000 else f"{f:.0f} Hz"
                panel.baseline.setText(f"<span style='color:{STATUS[lvl]}'>●</span> Δ baseline: "
                                       f"<b>{worst.delta_db:+.1f} dB</b> em {f_txt} (eixo {worst.axis.upper()})")
            else:
                panel.baseline.setText(f"<span style='color:{INK2}'>baseline: —</span>")

            active = self.alarms.active_for(nid)
            if active:
                level = "critical" if any(e.rule.level == "alarm" for e in active) else "warning"
                panel.set_status("; ".join(f"{e.rule.name} ({e.axis})" for e in active), level)
            else:
                panel.set_status("OK" if self.alarms.rules else "OK (limiares ainda não definidos)", "good")

            amps = {a: res.axes[a].spectrum.amp for a in AXES}
            freqs = res.axes["x"].spectrum.freqs
            amp = self._axis_amp(amps, axis_sel)
            if axis_sel.startswith("XYZ"):
                kk = 1 + int(np.argmax(amp[1:]))
                f_dom, a_dom = freqs[kk], amp[kk]
            else:
                ar = res.axes[axis_sel.lower()]
                f_dom, a_dom = ar.dom_freq, ar.dom_amp
            amp = np.maximum(amp, 1e-7)
            self.fft_curves[nid].setData(freqs[1:], amp[1:])
            self.fft_peaks[nid].setData([f_dom], [max(a_dom, 1e-7)])
            lbl = self.fft_legend.getLabel(self.fft_curves[nid])
            if lbl is not None:
                lbl.setText(f"Mancal {nid} — pico {f_dom:.1f} Hz ({a_dom:.3g} {unit})")
            self.spectro[nid].push(freqs, amp / k_unit)
            if self.tabs.currentIndex() == 1:
                self.spectro[nid].redraw()

        if self.cb_range.currentText() != "toda":
            self._apply_freq_range()  # setLogMode/setData religam o auto-ajuste do pyqtgraph

        rec = f" · ● GRAVANDO {self.rec.bytes_written / 1e6:.1f} MB" if self.rec.recording else ""
        cap = ""
        if self._capture_end:
            cap = f" · capturando baseline… {max(0, self._capture_end - time.monotonic()):.0f} s"
        warn = f" · {self.hub.warnings[-1]}" if self.hub.warnings else ""
        if not self.statusBar().currentMessage() or rec or cap:
            self.statusBar().showMessage(f"portas: {self.hub.port_map}{rec}{cap}{warn}")

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
