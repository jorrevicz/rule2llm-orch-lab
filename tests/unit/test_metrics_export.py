"""Extração das métricas de fila, containers e Ollama no host (M7-T05; D-10, D-26)."""

import csv
import json
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

import psutil
import pytest

from scripts.metrics.host_sampler import HostProcessSampler, HostSample
from scripts.metrics.prometheus_export import (
    CONTAINER_STATS_FILE,
    QUEUE_METRICS_FILE,
    QUEUE_SERIES,
    export_container_stats,
    export_queue_metrics,
    iso,
)

T0 = 1_790_000_000


def _matrix(metric: dict, values: list) -> dict:
    return {"metric": metric, "values": [[T0 + i, str(v)] for i, v in enumerate(values)]}


def fake_answer(query: str) -> list:
    if query.startswith("rabbitmq_queue_"):
        if query == "rabbitmq_queue_messages_published_total":
            return [_matrix({"queue": "inventory.primary"}, [0, 4])]
        return [_matrix({"queue": q}, [0, 2]) for q in ("inventory.primary", "tasks.dlq")]
    if "container_" in query:
        value = [5.5, 6.5] if "cpu" in query else [1024, 2048]
        return [_matrix({"container_label_com_docker_compose_service": s}, value) for s in ("orders-api", "rabbitmq")]
    return [_matrix({}, [120.0, 130.0] if "cpu" in query else [10, 20])]


@pytest.fixture
def prometheus():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)["query"][0]
            body = json.dumps({"status": "success", "data": {"result": fake_answer(query)}}).encode()
            self.send_response(200)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def _rows(path):
    with path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_queue_metrics_have_one_row_per_queue_and_instant(tmp_path, prometheus):
    export_queue_metrics(tmp_path, T0, T0 + 1, 1, prometheus)

    rows = _rows(tmp_path / QUEUE_METRICS_FILE)
    assert list(rows[0]) == ["timestamp", "queue", *QUEUE_SERIES]
    assert [(r["timestamp"], r["queue"]) for r in rows] == [
        (iso(T0), "inventory.primary"), (iso(T0), "tasks.dlq"), (iso(T0 + 1), "inventory.primary"), (iso(T0 + 1), "tasks.dlq")
    ]
    assert rows[2]["messages_ready"] == "2" and rows[2]["published_total"] == "4"
    assert rows[1]["published_total"] == ""  # série inexistente: vazio, nunca 0 inventado


def test_container_stats_merge_containers_host_and_ollama(tmp_path, prometheus):
    ollama = [HostSample(iso(T0), "ollama-host", 250.0, 5_000_000_000, 2)]

    export_container_stats(tmp_path, T0, T0 + 1, 1, ollama, prometheus)

    rows = _rows(tmp_path / CONTAINER_STATS_FILE)
    by_component = {(r["component"], r["source"]) for r in rows}
    assert by_component == {("orders-api", "cadvisor"), ("rabbitmq", "cadvisor"), ("host", "node-exporter"), ("ollama-host", "psutil")}
    ollama_row = next(r for r in rows if r["component"] == "ollama-host")
    assert (ollama_row["cpu_percent"], ollama_row["memory_bytes"]) == ("250.0", "5000000000")


def test_sampler_measures_cpu_time_of_the_process_tree():
    # O consumo vem de um processo filho (como o runner do Ollama sob `ollama serve`) e esta
    # thread só dorme. Ocupar a CPU aqui mesmo disputava o GIL com a varredura do /proc e,
    # no servidor (270 processos), cada amostra levava ~5 s (M8-T24).
    busy = subprocess.Popen([sys.executable, "-c", "while True: pass"])
    try:
        me = psutil.Process()
        deadline = time.monotonic() + 5
        while len(me.children()) < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        sampler = HostProcessSampler(0.2, find_root=lambda: me, component="self")
        sampler.start()
        time.sleep(1.0)
        samples = sampler.stop()
    finally:
        busy.kill()
        busy.wait()

    assert len(samples) >= 3
    assert samples[0].cpu_percent is None  # a primeira amostra não tem intervalo anterior
    assert any(s.cpu_percent and s.cpu_percent > 10 for s in samples[1:])  # CPU do filho somada
    assert all(s.processes >= 2 for s in samples)
    assert all(s.memory_bytes > 0 and s.component == "self" for s in samples)


def test_sampler_records_absence_without_inventing_values():
    sampler = HostProcessSampler(0.1, find_root=lambda: None)
    sampler.start()
    time.sleep(0.25)
    samples = sampler.stop()

    assert samples and all(s.cpu_percent is None and s.memory_bytes is None and s.processes == 0 for s in samples)
