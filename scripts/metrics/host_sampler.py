"""CPU/RAM do processo do Ollama no host (M7-T05; D-08, D-10, D-26).

O Ollama roda fora dos containers (D-08), então o cAdvisor não o vê. Este amostrador
lê, a cada intervalo, o processo `ollama serve` e todos os seus descendentes (o runner
do modelo pode ser um processo filho, criado ao carregar o modelo):

- `cpu_percent`: tempo de CPU (usuário + sistema) consumido no intervalo, dividido pelo
  tempo de parede, em % de um núcleo — a mesma escala do cAdvisor e do `docker stats`;
- `memory_bytes`: soma do RSS.

Usa o tempo de CPU acumulado (psutil), preciso em macOS e Linux; GPU não é medida (no
macOS exigiria `sudo powermetrics`) e fica fora da tabela, sem valor inventado.
"""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import psutil

from shared.timestamps import utc_now_iso

COMPONENT = "ollama-host"


@dataclass(frozen=True)
class HostSample:
    timestamp: str
    component: str
    cpu_percent: float | None
    memory_bytes: int | None
    processes: int


def find_ollama_server() -> psutil.Process | None:
    for process in psutil.process_iter(["name", "cmdline"]):
        name = (process.info["name"] or "").lower()
        cmdline = process.info["cmdline"] or []
        if "ollama" in name and "serve" in cmdline:
            return process
    return None


def _tree(root: psutil.Process) -> list[psutil.Process]:
    try:
        return [root, *root.children(recursive=True)]
    except psutil.Error:
        return []


def _cpu_seconds(processes: list[psutil.Process]) -> tuple[float, int, int]:
    cpu, rss, alive = 0.0, 0, 0
    for process in processes:
        try:
            times = process.cpu_times()
            cpu += times.user + times.system
            rss += process.memory_info().rss
            alive += 1
        except psutil.Error:  # processo terminou entre a listagem e a leitura
            continue
    return cpu, rss, alive


class HostProcessSampler:
    """Amostra o processo em uma thread própria até `stop()`."""

    def __init__(
        self,
        interval_seconds: float,
        *,
        find_root: Callable[[], psutil.Process | None] = find_ollama_server,
        component: str = COMPONENT,
    ) -> None:
        self.interval = interval_seconds
        self.component = component
        self._find_root = find_root
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self.samples: list[HostSample] = []

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> list[HostSample]:
        self._stop.set()
        self._thread.join()
        return self.samples

    def _run(self) -> None:
        previous: tuple[float, float] | None = None  # (instante, CPU acumulada)
        next_tick = time.monotonic()
        while not self._stop.is_set():
            root = self._find_root()
            now = time.monotonic()
            if root is None:
                self.samples.append(HostSample(utc_now_iso(), self.component, None, None, 0))
                previous = None
            else:
                cpu, rss, alive = _cpu_seconds(_tree(root))
                percent = None
                if previous is not None and now > previous[0]:
                    percent = round(100 * max(cpu - previous[1], 0.0) / (now - previous[0]), 2)
                self.samples.append(HostSample(utc_now_iso(), self.component, percent, rss, alive))
                previous = (now, cpu)
            next_tick += self.interval
            self._stop.wait(max(next_tick - time.monotonic(), 0))
