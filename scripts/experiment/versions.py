"""Versões e hardware lidos do ambiente em execução (M7-T06; metodologia Código 12, §4.4.1).

Tudo é lido de fontes reais (containers, Docker, host); o que não pode ser obtido de
forma confiável fica `null` — nunca inventado (CLAUDE §44).
"""

import json
import platform
import re
import subprocess

from scripts.pilot.environment import InspectionError, compose


def _run(*command: str) -> str | None:
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=30)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    return completed.stdout.strip() or None if completed.returncode == 0 else None


def _in_container(service: str, *command: str) -> str | None:
    try:
        return compose("exec", "-T", service, *command).strip() or None
    except InspectionError:
        return None


def _first_version(text: str | None) -> str | None:
    match = re.search(r"\d+\.\d+(\.\d+)?", text or "")
    return match.group(0) if match else None


def compose_images() -> dict[str, dict]:
    output = compose("images", "--format", "json")
    try:
        rows = json.loads(output)
    except json.JSONDecodeError:
        rows = [json.loads(line) for line in output.splitlines() if line.strip()]
    return {
        row.get("Service") or row.get("ContainerName"): {"image": f"{row.get('Repository')}:{row.get('Tag')}", "id": row.get("ID")}
        for row in rows
    }


def software_versions() -> dict:
    return {
        "python_version": _first_version(_in_container("orders-api", "python", "--version")),
        "celery_version": _first_version(_in_container("orders-worker", "celery", "--version")),
        "rabbitmq_version": _first_version(_in_container("rabbitmq", "rabbitmqctl", "version")),
        "docker_version": _run("docker", "version", "--format", "{{.Server.Version}}"),
        "docker_compose_version": _run("docker", "compose", "version", "--short"),
        "images": compose_images(),
    }


def host_hardware() -> dict:
    system = platform.system()
    cpu = ram_gb = gpu = None
    if system == "Darwin":
        cpu = _run("sysctl", "-n", "machdep.cpu.brand_string")
        memory = _run("sysctl", "-n", "hw.memsize")
        ram_gb = round(int(memory) / 1024**3, 1) if memory else None
    elif system == "Linux":
        cpuinfo = _run("cat", "/proc/cpuinfo") or ""
        cpu = next((line.split(":", 1)[1].strip() for line in cpuinfo.splitlines() if line.startswith("model name")), None)
        meminfo = _run("cat", "/proc/meminfo") or ""
        total_kb = next((int(line.split()[1]) for line in meminfo.splitlines() if line.startswith("MemTotal")), None)
        ram_gb = round(total_kb / 1024**2, 1) if total_kb else None
        gpu = _run("nvidia-smi", "--query-gpu=name", "--format=csv,noheader")
    return {
        "platform": platform.platform(),
        "system": system,
        "machine": platform.machine(),
        "cpu": cpu,
        "ram_gb": ram_gb,
        "gpu": gpu,  # null no macOS (GPU integrada, sem leitura confiável sem sudo)
    }
