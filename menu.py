"""Menu interativo dos procedimentos do experimento (M8-T07).

    .venv/bin/python menu.py

Escolhe entre o executor do protocolo (`run_experiment`), os scripts que não são
automatizados por ele (dataset, schemas, testes de piloto, bancada do LLM, ambiente) e o
protocolo completo ponta a ponta: todos os cenários × Rules e LLM, repetido N vezes ou
continuamente.

Protocolo completo:
- ciclo k = repetição k: cada ciclo é `schedule(cenários, motores, 1, first=k)`, então a
  abordagem que abre a repetição continua alternando entre ciclos (metodologia §4.4.1);
- cada execução é um processo `run_experiment` em sessão própria: o Ctrl+C do terminal
  não chega a ele nem ao `docker compose`. 1º Ctrl+C = parada segura (a execução em
  andamento termina normalmente e o laço para); 2º = interrupção forçada da execução;
- ao fim de cada ciclo, confere os artefatos de cada execução válida (os obrigatórios e
  o `metrics_summary.csv` da consolidação) e imprime o resumo;
- entre ciclos, aviso + espera de 10 s: nenhuma execução está em andamento, é o momento
  seguro de parar.

Só cria execuções de piloto: `open_execution` recusa fase diferente de `pilot`.
"""

import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import FrameType

from scripts.experiment.run_experiment import DEFAULT_DATA_ROOT, REQUIRED_ARTIFACTS, schedule
from scripts.pilot.environment import REPO_ROOT
from scripts.pilot.llm_readiness import DEFAULT_OLLAMA_URL
from scripts.pilot.new_execution import available_scenarios, git_commit
from services.orders.app.llm.ollama_client import LLMRuntimeError, LLMTimeout, OllamaClient
from shared.artifacts import execution_dir
from shared.config import ExperimentConfig, load_experiment_config

Ask = Callable[[str], str]
Plan = list[tuple[str, str, int]]

RUN_EXPERIMENT = "scripts.experiment.run_experiment"
DEFAULT_ENGINES = ("RULES", "LLM")
CONSOLIDATED_ARTIFACT = "metrics_summary.csv"
SAFE_STOP_WINDOW_S = 10
PREFLIGHT_TIMEOUT_S = 5
DECISION_SCENARIOS = ("inventory_down", "inventory_paused")


# --- entrada do usuário -------------------------------------------------------------


def parse_selection(text: str, options: list[str]) -> list[str] | None:
    """Vazio ou "todos" → todas as opções; "1,3" ou "1 3" → as escolhidas, na ordem digitada.

    Número fora da lista ou texto não numérico → `None` (seleção inválida).
    """
    tokens = text.replace(",", " ").split()
    if not tokens or tokens == ["todos"]:
        return list(options)
    chosen: list[str] = []
    for token in tokens:
        if not token.isdigit() or not 1 <= int(token) <= len(options):
            return None
        option = options[int(token) - 1]
        if option not in chosen:
            chosen.append(option)
    return chosen


def parse_cycles(text: str) -> int | None:
    """Número de ciclos: inteiro ≥ 1, vazio = 1, "c" = contínuo (`0`). Inválido → `None`."""
    text = text.strip().lower()
    if not text:
        return 1
    if text == "c":
        return 0
    return int(text) if text.isdigit() and int(text) >= 1 else None


def ask_int(ask: Ask, prompt: str, default: int, minimum: int = 1) -> int:
    while True:
        text = ask(f"{prompt} [{default}]: ").strip()
        if not text:
            return default
        if text.isdigit() and int(text) >= minimum:
            return int(text)
        print(f"  Digite um inteiro ≥ {minimum}.")


def ask_yes(ask: Ask, prompt: str) -> bool:
    return ask(f"{prompt} [s/N]: ").strip().lower() in {"s", "sim", "y", "yes"}


def ask_selection(ask: Ask, prompt: str, options: list[str]) -> list[str]:
    for number, option in enumerate(options, 1):
        print(f"  {number}. {option}")
    while True:
        chosen = parse_selection(ask(f"{prompt} (números separados por vírgula, Enter = todos): "), options)
        if chosen:
            return chosen
        print("  Seleção inválida.")


def ask_engines(ask: Ask) -> list[str]:
    options = [["RULES", "LLM"], ["LLM", "RULES"], ["RULES"], ["LLM"]]
    print("  1. RULES e LLM (RULES abre as repetições ímpares)")
    print("  2. LLM e RULES (LLM abre as repetições ímpares)")
    print("  3. só RULES")
    print("  4. só LLM")
    while True:
        text = ask("Motores [1]: ").strip() or "1"
        if text in {"1", "2", "3", "4"}:
            return options[int(text) - 1]
        print("  Opção inválida.")


# --- execução de procedimentos -------------------------------------------------------


def module_command(module: str, *args: str) -> list[str]:
    return [sys.executable, "-m", module, *args]


def run_experiment_command(scenario: str, engine: str, repetition: int) -> list[str]:
    """Uma execução do protocolo (as inválidas são repetidas dentro do `run_experiment`)."""
    return module_command(
        RUN_EXPERIMENT, "--scenario", scenario, "--engine", engine,
        "--repetitions", "1", "--first-repetition", str(repetition),
    )


def _display(command: list[str]) -> str:
    executable = Path(command[0])
    if executable.is_relative_to(REPO_ROOT):
        executable = executable.relative_to(REPO_ROOT)
    return " ".join([str(executable), *command[1:]])


def run_procedure(command: list[str]) -> int:
    """Roda um procedimento em primeiro plano.

    O Ctrl+C chega também ao procedimento, que encerra com a própria limpeza (ex.:
    `decision_scenarios` restaura o `inventory-worker`); o menu só espera ele terminar.
    """
    print(f"\n$ {_display(command)}", flush=True)
    child = subprocess.Popen(command, cwd=REPO_ROOT)
    while True:
        try:
            returncode = child.wait()
            break
        except KeyboardInterrupt:
            print("\nCtrl+C: aguardando o procedimento encerrar...", flush=True)
    print(f"\n[código de saída {returncode}]")
    return returncode


# --- protocolo completo --------------------------------------------------------------


class StopRequest:
    """Ctrl+C durante o protocolo completo: o 1º pede parada segura; o 2º força."""

    def __init__(self) -> None:
        self.count = 0
        self.forced = False
        self.child: subprocess.Popen | None = None

    @property
    def requested(self) -> bool:
        return self.count >= 1

    def handle(self, signum: int, frame: FrameType | None) -> None:
        self.count += 1
        running = self.child is not None and self.child.poll() is None
        if not running:
            print("\nParada solicitada.", flush=True)
        elif self.count == 1:
            print(
                "\n⚠ Parada solicitada: a execução em andamento termina normalmente e o laço para."
                " Ctrl+C de novo força a interrupção (a execução fica incompleta).",
                flush=True,
            )
        elif self.child is not None:
            print("\n⚠ Interrupção forçada da execução em andamento.", flush=True)
            self.forced = True
            os.killpg(self.child.pid, signal.SIGINT)


def parse_result_line(line: str) -> dict | None:
    """Linha de resultado que o `run_experiment` imprime por tentativa; demais → `None`."""
    line = line.strip()
    if not line.startswith("{"):
        return None
    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        return None
    if isinstance(value, dict) and "execution_id" in value and "run_status" in value:
        return value
    return None


def run_child(command: list[str], stop: StopRequest) -> list[dict]:
    """Execução em sessão própria; a saída é repassada ao terminal e os resultados guardados."""
    print(f"$ {_display(command)}", flush=True)
    child = subprocess.Popen(
        command,
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        text=True,
        bufsize=1,
        start_new_session=True,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    stop.child = child
    results = []
    try:
        for line in child.stdout or ():
            print(line, end="", flush=True)
            result = parse_result_line(line)
            if result is not None:
                results.append(result)
        child.wait()
    finally:
        stop.child = None
    return results


def cycle_plan(scenarios: list[str], engines: list[str], repetition: int) -> Plan:
    return schedule(scenarios, engines, 1, first=repetition)


@dataclass
class CycleReport:
    repetition: int
    results: list[dict] = field(default_factory=list)
    not_run: Plan = field(default_factory=list)
    interrupted: tuple[str, str, int] | None = None


def run_cycle(plan: Plan, stop: StopRequest, runner: Callable[[list[str], StopRequest], list[dict]] = run_child) -> CycleReport:
    report = CycleReport(repetition=plan[0][2])
    for index, (scenario, engine, repetition) in enumerate(plan):
        if stop.requested:
            report.not_run = plan[index:]
            break
        print(f"\n▶ {scenario} · {engine} · repetição {repetition} ({index + 1}/{len(plan)})", flush=True)
        report.results += runner(run_experiment_command(scenario, engine, repetition), stop)
        if stop.forced:
            report.interrupted = (scenario, engine, repetition)
            report.not_run = plan[index + 1:]
            break
    return report


def missing_artifacts(directory: Path) -> list[str]:
    return [name for name in (*REQUIRED_ARTIFACTS, CONSOLIDATED_ARTIFACT) if not (directory / name).is_file()]


def print_cycle_report(report: CycleReport, data_root: Path = DEFAULT_DATA_ROOT) -> None:
    print(f"\n=== Ciclo da repetição {report.repetition}: resumo ===")
    print(f"{'execution_id':<13} {'cenário':<21} {'motor':<6} {'rep':>3}  {'run_status':<10} artefatos")
    for result in report.results:
        directory = execution_dir(data_root, result["execution_id"])
        if result["run_status"] == "VALID":
            missing = missing_artifacts(directory)
            artifacts = "ok" if not missing else f"FALTANDO: {', '.join(missing)}"
        else:
            artifacts = f"— ({str(result.get('invalid_reason'))[:70]})"
        print(
            f"{result['execution_id']:<13} {result['scenario_id']:<21} {result['decision_engine']:<6} "
            f"{result['repetition_id']:>3}  {result['run_status']:<10} {artifacts}"
        )
    if report.results:
        print(f"Artefatos em {data_root.relative_to(REPO_ROOT) if data_root.is_relative_to(REPO_ROOT) else data_root}/<fase>/<execution_id>/")
    if report.interrupted:
        print(f"⚠ Interrompida à força (diretório incompleto, sem run_status final): {report.interrupted}")
    if report.not_run:
        print(f"Não executadas neste ciclo ({len(report.not_run)}): {report.not_run}")


def countdown(seconds: int, stop: StopRequest, sleep: Callable[[float], None] = time.sleep) -> bool:
    """Janela segura entre ciclos. `True` se terminou sem pedido de parada."""
    for remaining in range(seconds, 0, -1):
        if stop.requested:
            print()
            return False
        print(f"\r  Próximo ciclo em {remaining:2d} s — Ctrl+C para encerrar ", end="", flush=True)
        sleep(1)
    print()
    return not stop.requested


def preflight(config: ExperimentConfig, engines: list[str]) -> list[str]:
    """Falhas óbvias da bancada antes de abrir execuções (evita inválidas em série)."""
    failures = []
    if config.experiment.phase != "pilot":
        failures.append(f"fase da config é '{config.experiment.phase}': o executor só abre execuções de piloto")
    try:
        docker = subprocess.run(["docker", "info"], capture_output=True, timeout=30)
        if docker.returncode != 0:
            failures.append("Docker não está respondendo (`docker info` falhou)")
    except (OSError, subprocess.TimeoutExpired) as error:
        failures.append(f"Docker indisponível: {error}")
    if "LLM" in engines:
        client = OllamaClient(DEFAULT_OLLAMA_URL, config.llm, timeout_seconds=PREFLIGHT_TIMEOUT_S)
        try:
            client.version()
        except (LLMRuntimeError, LLMTimeout) as error:
            failures.append(f"Ollama não responde em {DEFAULT_OLLAMA_URL} ({error}); inicie com `open -a Ollama`")
    return failures


def full_protocol(ask: Ask) -> None:
    config = load_experiment_config()
    scenarios = available_scenarios()
    engines = list(config.experiment.execution_order or DEFAULT_ENGINES)
    print("\nProtocolo completo ponta a ponta")
    print(f"  Cenários ({len(scenarios)}): {', '.join(scenarios)}")
    print(f"  Motores: {' e '.join(engines)} ({engines[0]} abre as repetições ímpares; alternância a cada ciclo)")
    print(f"  Execuções por ciclo: {len(scenarios) * len(engines)}; inválidas repetidas pelo run_experiment")
    while (cycles := parse_cycles(ask("Número de ciclos (inteiro ≥ 1) ou 'c' para contínuo [1]: "))) is None:
        print("  Digite um inteiro ≥ 1 ou 'c'.")
    first = ask_int(ask, "Repetição inicial", 1)

    failures = preflight(config, engines)
    if failures:
        print("\nPré-verificação reprovada:")
        for failure in failures:
            print(f"  - {failure}")
        return
    planned = "contínuo (até Ctrl+C)" if cycles == 0 else f"{cycles} ciclo(s), repetições {first}–{first + cycles - 1}"
    print(f"\nPlano: {planned}. Ctrl+C durante uma execução = parada segura ao fim dela.")
    if not ask_yes(ask, "Iniciar?"):
        return

    stop = StopRequest()
    previous = signal.signal(signal.SIGINT, stop.handle)
    reports: list[CycleReport] = []
    try:
        repetition = first
        while True:
            report = run_cycle(cycle_plan(scenarios, engines, repetition), stop)
            reports.append(report)
            print_cycle_report(report)
            if stop.requested or (cycles and len(reports) >= cycles):
                break
            print(
                f"\n⚠ Ciclo da repetição {repetition} concluído. Nenhuma execução em andamento: é seguro parar agora."
                f"\n  O ciclo da repetição {repetition + 1} começa em {SAFE_STOP_WINDOW_S} s.",
                flush=True,
            )
            if not countdown(SAFE_STOP_WINDOW_S, stop):
                break
            repetition += 1
    finally:
        signal.signal(signal.SIGINT, previous)
    print_session_summary(reports)


def print_session_summary(reports: list[CycleReport]) -> None:
    results = [result for report in reports for result in report.results]
    complete = sum(1 for report in reports if not report.not_run and report.interrupted is None)
    valid = sum(1 for result in results if result["run_status"] == "VALID")
    print("\n=== Sessão encerrada ===")
    print(f"  Ciclos completos: {complete} de {len(reports)}")
    print(f"  Tentativas: {len(results)} ({valid} VALID, {len(results) - valid} INVALID)")
    interrupted = [report.interrupted for report in reports if report.interrupted]
    if interrupted:
        print(f"  Interrompidas à força: {interrupted}")


# --- itens do menu -------------------------------------------------------------------


def custom_experiment(ask: Ask) -> None:
    print("\nCenários:")
    scenarios = ask_selection(ask, "Cenários", available_scenarios())
    engines = ask_engines(ask)
    repetitions = ask_int(ask, "Repetições", 1)
    first = ask_int(ask, "Repetição inicial", 1)
    attempts = ask_int(ask, "Tentativas por execução inválida", 3)
    args = [arg for scenario in scenarios for arg in ("--scenario", scenario)]
    args += [arg for engine in engines for arg in ("--engine", engine)]
    args += ["--repetitions", str(repetitions), "--first-repetition", str(first), "--max-attempts", str(attempts)]
    print("Aviso: aqui o Ctrl+C interrompe a execução em andamento (a parada segura é do item 1).")
    run_procedure(module_command(RUN_EXPERIMENT, *args))


def check_dataset(ask: Ask) -> None:
    run_procedure(module_command("scripts.datasets.generate_dataset", "--check"))


def regenerate_dataset(ask: Ask) -> None:
    print("\nRegenerar sobrescreve o catálogo e o dataset versionados (determinístico pela seed).")
    print("Mudar o dataset tem impacto metodológico (CLAUDE §43).")
    if ask_yes(ask, "Continuar?"):
        run_procedure(module_command("scripts.datasets.generate_dataset"))


def export_schemas(ask: Ask) -> None:
    print("\nReescreve contracts/*.schema.json a partir dos modelos; mudança de contrato tem impacto metodológico.")
    if ask_yes(ask, "Continuar?"):
        run_procedure(module_command("scripts.contracts.export_schemas"))


def smoke_test(ask: Ask) -> None:
    orders = ask_int(ask, "Pedidos", 1)
    run_procedure(module_command("scripts.pilot.smoke_test", "--orders", str(orders)))


def idempotency_test(ask: Ask) -> None:
    run_procedure(module_command("scripts.pilot.duplicate_message_test"))


def decision_scenarios(ask: Ask) -> None:
    for number, scenario in enumerate(DECISION_SCENARIOS, 1):
        print(f"  {number}. {scenario}")
    while (text := ask("Cenário [1]: ").strip() or "1") not in {"1", "2"}:
        print("  Opção inválida.")
    args = [DECISION_SCENARIOS[int(text) - 1]]
    if ask_yes(ask, "Só observar as decisões (--observe; modo para o LLM)?"):
        args.append("--observe")
    run_procedure(module_command("scripts.pilot.decision_scenarios", *args))


def llm_bench(ask: Ask) -> None:
    repetitions = ask_int(ask, "Repetições por estado", 5)
    run_procedure(module_command("scripts.pilot.llm_bench", "--repetitions", str(repetitions)))


def compose_up(ask: Ask) -> None:
    run_procedure(["docker", "compose", "up", "-d", "--build", "--wait"])


def compose_down(ask: Ask) -> None:
    run_procedure(["docker", "compose", "down"])


@dataclass(frozen=True)
class Item:
    group: str
    label: str
    action: Callable[[Ask], object]


NEEDS_ENVIRONMENT = " (exige o ambiente no ar: item 10)"
ITEMS: dict[str, Item] = {
    "1": Item("Protocolo", "Protocolo completo ponta a ponta (todos os cenários × Rules e LLM), repetível", full_protocol),
    "2": Item("Protocolo", "Executor do protocolo personalizado (run_experiment)", custom_experiment),
    "3": Item("Preparação", "Dataset: verificar (--check)", check_dataset),
    "4": Item("Preparação", "Dataset: regenerar", regenerate_dataset),
    "5": Item("Preparação", "Exportar schemas dos contratos", export_schemas),
    "6": Item("Testes de piloto", "Smoke test do fluxo normal (M1)" + NEEDS_ENVIRONMENT, smoke_test),
    "7": Item("Testes de piloto", "Idempotência: redelivery e nova tentativa (M2)" + NEEDS_ENVIRONMENT, idempotency_test),
    "8": Item("Testes de piloto", "Cenários de decisão (M4)" + NEEDS_ENVIRONMENT, decision_scenarios),
    "9": Item("Testes de piloto", "Bancada de viabilidade do LLM (M5)" + NEEDS_ENVIRONMENT, llm_bench),
    "10": Item("Ambiente", "Subir ambiente (docker compose up -d --build --wait; execução PILOT_0000)", compose_up),
    "11": Item("Ambiente", "Derrubar ambiente (docker compose down)", compose_down),
}


def print_menu() -> None:
    config = load_experiment_config()
    commit = git_commit()
    print("\n" + "=" * 72)
    print("rule2llm-orch-lab — procedimentos do experimento")
    print(f"fase: {config.experiment.phase} · commit: {commit[:7] + ('-dirty' if commit.endswith('-dirty') else '') if commit else '?'}")
    print("=" * 72)
    group = None
    for key, item in ITEMS.items():
        if item.group != group:
            group = item.group
            print(f"\n {group}")
        print(f"  {key:>2}. {item.label}")
    print("\n   0. Sair")


def main(ask: Ask = input) -> int:
    os.chdir(REPO_ROOT)  # config e scripts usam caminhos relativos à raiz
    try:
        while True:
            print_menu()
            choice = ask("\nOpção: ").strip()
            if choice == "0":
                return 0
            item = ITEMS.get(choice)
            if item is None:
                print("Opção inválida.")
                continue
            item.action(ask)
            ask("\nEnter para voltar ao menu...")
    except (EOFError, KeyboardInterrupt):
        print()
        return 0


if __name__ == "__main__":
    sys.exit(main())
