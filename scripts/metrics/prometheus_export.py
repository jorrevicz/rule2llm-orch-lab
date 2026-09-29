"""Extrai do Prometheus as séries da execução para CSV (M7-T05; D-10, D-26).

Executado ao fim de cada execução, antes do próximo reset (o `down -v` apaga a base do
Prometheus). Passo = intervalo de amostragem; janela = do início ao fim da execução.

- `queue_metrics.csv`: por fila e instante — mensagens (total, prontas, sem ack),
  consumidores e contadores acumulados de publicação, entrega, reentrega e ack
  (`rabbitmq_prometheus`, métricas por fila). Célula vazia = série ainda inexistente
  naquele instante (o RabbitMQ só cria o contador no primeiro evento; a taxa de CPU precisa
  de histórico) — nunca preenchida com valor inventado;
- `container_stats.csv`: por componente e instante — CPU (% de um núcleo) e memória
  (bytes): containers pelo cAdvisor (nome do serviço do compose), o host pelo
  node-exporter e o processo do Ollama no host pelo amostrador próprio.
"""

import csv
import json
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from scripts.metrics.host_sampler import HostSample

PROMETHEUS_URL = "http://localhost:9090"
QUEUE_METRICS_FILE = "queue_metrics.csv"
CONTAINER_STATS_FILE = "container_stats.csv"

QUEUE_SERIES = {
    "messages": "rabbitmq_queue_messages",
    "messages_ready": "rabbitmq_queue_messages_ready",
    "messages_unacked": "rabbitmq_queue_messages_unacked",
    "consumers": "rabbitmq_queue_consumers",
    "published_total": "rabbitmq_queue_messages_published_total",
    "delivered_total": "rabbitmq_queue_messages_delivered_total",
    "redelivered_total": "rabbitmq_queue_messages_redelivered_total",
    "acked_total": "rabbitmq_queue_messages_acked_total",
}
SERVICE_LABEL = "container_label_com_docker_compose_service"
RATE_WINDOW = "3s"  # 3 amostras de 1 s: a taxa sai com o mínimo de 2 pontos mesmo com atraso


def container_queries() -> dict[str, str]:
    selector = f'{{{SERVICE_LABEL}!=""}}'
    return {
        "cpu_percent": f"sum by ({SERVICE_LABEL}) (rate(container_cpu_usage_seconds_total{selector}[{RATE_WINDOW}])) * 100",
        "memory_bytes": f"sum by ({SERVICE_LABEL}) (container_memory_working_set_bytes{selector})",
    }


HOST_QUERIES = {
    "cpu_percent": f'sum(rate(node_cpu_seconds_total{{mode!="idle"}}[{RATE_WINDOW}])) * 100',
    "memory_bytes": "node_memory_MemTotal_bytes - node_memory_MemAvailable_bytes",
}


def iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def query_range(query: str, start: float, end: float, step: float, url: str = PROMETHEUS_URL) -> list[dict]:
    params = urllib.parse.urlencode({"query": query, "start": start, "end": end, "step": step})
    with urllib.request.urlopen(f"{url}/api/v1/query_range?{params}", timeout=30) as response:
        body = json.load(response)
    if body.get("status") != "success":
        raise RuntimeError(f"prometheus query failed: {body}")
    return body["data"]["result"]


def _number(value: str) -> float | int:
    number = float(value)
    return int(number) if number.is_integer() else round(number, 4)


def export_queue_metrics(directory: Path, start: float, end: float, step: float, url: str = PROMETHEUS_URL) -> int:
    rows: dict[tuple[float, str], dict] = defaultdict(dict)
    for column, metric in QUEUE_SERIES.items():
        for series in query_range(metric, start, end, step, url):
            queue = series["metric"]["queue"]
            for timestamp, value in series["values"]:
                rows[(timestamp, queue)][column] = _number(value)
    with (directory / QUEUE_METRICS_FILE).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["timestamp", "queue", *QUEUE_SERIES])
        writer.writeheader()
        for (timestamp, queue), values in sorted(rows.items()):
            writer.writerow({"timestamp": iso(timestamp), "queue": queue, **values})
    return len(rows)


def export_container_stats(
    directory: Path,
    start: float,
    end: float,
    step: float,
    host_samples: list[HostSample],
    url: str = PROMETHEUS_URL,
) -> int:
    rows: dict[tuple[str, str, str], dict] = defaultdict(dict)
    for column, query in container_queries().items():
        for series in query_range(query, start, end, step, url):
            component = series["metric"][SERVICE_LABEL]
            for timestamp, value in series["values"]:
                rows[(iso(timestamp), component, "cadvisor")][column] = _number(value)
    for column, query in HOST_QUERIES.items():
        for series in query_range(query, start, end, step, url):
            for timestamp, value in series["values"]:
                rows[(iso(timestamp), "host", "node-exporter")][column] = _number(value)
    for sample in host_samples:
        rows[(sample.timestamp, sample.component, "psutil")].update(
            cpu_percent=sample.cpu_percent, memory_bytes=sample.memory_bytes
        )
    with (directory / CONTAINER_STATS_FILE).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["timestamp", "component", "source", "cpu_percent", "memory_bytes"])
        writer.writeheader()
        for (timestamp, component, source), values in sorted(rows.items()):
            writer.writerow({"timestamp": timestamp, "component": component, "source": source, **values})
    return len(rows)
