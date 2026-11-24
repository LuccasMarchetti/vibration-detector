"""Teste de integracao com o FIRMWARE REAL rodando no emulador QEMU do ESP32.

Opcional: so roda com a variavel VIBMON_QEMU_URL definida. Como preparar:

    cd firmware && idf.py build && idf.py qemu         # gera build/qemu_flash.bin
    # depois rode o QEMU com a UART numa porta TCP:
    qemu-system-xtensa -M esp32 -m 4M -drive file=build/qemu_flash.bin,if=mtd,format=raw \\
        -nic user,model=open_eth -nographic -serial tcp::5555,server,nowait -monitor none
    VIBMON_QEMU_URL=socket://localhost:5555 python -m pytest tests/test_firmware_qemu.py -v

No emulador nao ha sensor: o firmware deve responder aos comandos mesmo assim
e reportar who_am_i = 0 no STATUS.
"""
import os
import time

import pytest

from vibmon import edge
from vibmon.edge import (CMD_CLEAR_BASE, CMD_GET_CONFIG, CMD_RESET_ALARM, CMD_SET_BASELINE, CMD_SET_CONFIG,
                         CMD_TEST_OUTPUT, EdgeConfig, command, parse_ack, parse_config_reply)
from vibmon.protocol import PKT_ACK, PKT_CONFIG, PKT_STATUS, StreamParser

URL = os.environ.get("VIBMON_QEMU_URL")
pytestmark = pytest.mark.skipif(not URL, reason="defina VIBMON_QEMU_URL para testar o firmware no QEMU")


@pytest.fixture(scope="module")
def link():
    import serial

    ser = serial.serial_for_url(URL, 921600, timeout=0.05)
    parser = StreamParser()
    node = 0
    statuses = []
    t_end = time.monotonic() + 4
    while time.monotonic() < t_end:
        for p in parser.feed(ser.read(4096)):
            node = p.node_id
            if p.type == PKT_STATUS:
                statuses.append(p)
    yield ser, parser, node, statuses
    ser.close()


def ask(link, cmd, payload=b"", node=None, timeout=2.0):
    ser, parser, my_node, _ = link
    ser.write(command(cmd, my_node if node is None else node, payload))
    t_end = time.monotonic() + timeout
    while time.monotonic() < t_end:
        for p in parser.feed(ser.read(4096)):
            if p.type == PKT_CONFIG and cmd == CMD_GET_CONFIG:
                return p
            if p.type == PKT_ACK and p.payload[0] == cmd:
                return p
    return None


def test_status_without_sensor(link):
    _, _, node, statuses = link
    assert node in (1, 2)
    assert statuses, "o firmware deve mandar STATUS (heartbeat) mesmo sem sensor"
    assert statuses[-1].status()["who_am_i"] == 0


def test_config_roundtrip_and_validation(link):
    cfg = EdgeConfig(n_window=1024, window="hamming", hold=5, overall_rms_warn=0.25,
                     overall_rms_trip=0.6, kurtosis_warn=6.0, baseline_db_warn=6.0)
    cfg.bands[0] = edge.EdgeBand(25, 35, 0.1, 0.3)
    p = ask(link, CMD_SET_CONFIG, cfg.to_bytes())
    assert p is not None and parse_ack(p)[1] == 0
    back, _ = parse_config_reply(ask(link, CMD_GET_CONFIG))
    assert back.to_bytes() == cfg.to_bytes()

    bad = bytearray(cfg.to_bytes())
    bad[2:4] = (999).to_bytes(2, "little")  # n_window invalido
    assert parse_ack(ask(link, CMD_SET_CONFIG, bytes(bad)))[1] == 2
    assert parse_ack(ask(link, CMD_SET_CONFIG, b"\x01\x02"))[1] == 1
    back, _ = parse_config_reply(ask(link, CMD_GET_CONFIG))
    assert back.to_bytes() == cfg.to_bytes(), "config invalida nao pode substituir a valida"


def test_baseline_upload_and_clear(link):
    import numpy as np

    from vibmon.baseline import build_node

    rng = np.random.default_rng(0)
    nb = build_node(rng.normal(0, 0.01, (20000, 3)), 6660, 4096)
    assert parse_ack(ask(link, CMD_SET_BASELINE, edge.baseline_payload(nb, 2048, "hann")))[1] == 0
    bad = bytearray(edge.baseline_payload(nb, 2048, "hann"))
    bad[2:4] = (4096).to_bytes(2, "little")  # janela que o ESP32 nao usa
    assert parse_ack(ask(link, CMD_SET_BASELINE, bytes(bad)))[1] == 2
    _, n_bands = parse_config_reply(ask(link, CMD_GET_CONFIG))
    assert n_bands == min(32, len(nb.bands))
    assert parse_ack(ask(link, CMD_CLEAR_BASE))[1] == 0
    assert parse_config_reply(ask(link, CMD_GET_CONFIG))[1] == 0


def test_misc_commands(link):
    assert parse_ack(ask(link, CMD_RESET_ALARM))[1] == 0
    assert parse_ack(ask(link, CMD_TEST_OUTPUT, b"\x01"))[1] == 0     # buzzer existe (GPIO25)
    assert parse_ack(ask(link, CMD_TEST_OUTPUT, b"\x09"))[1] == 5     # saida inexistente
    assert parse_ack(ask(link, 0x8F))[1] == 3                         # comando desconhecido


def test_command_for_other_node_is_ignored(link):
    _, _, node, _ = link
    assert ask(link, CMD_RESET_ALARM, node=3 - node, timeout=1.0) is None
