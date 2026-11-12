"""Manifesto de hashes da configuração congelada (M8-T04; RNF-027, RNF-028; CLAUDE §19, §31).

    .venv/bin/python -m scripts.experiment.freeze_manifest --check   # compara com o manifesto
    .venv/bin/python -m scripts.experiment.freeze_manifest --write   # regrava (só no congelamento)

Lista, por grupo, o SHA-256 de cada artefato que define o experimento: prompt, Rules,
Validator, Executor, StateBuilder, LLM e contratos (camada de decisão); config, cenários e
plano de análise; dataset e catálogo; consolidação, rubrica e comparação; imagens e
bibliotecas. O commit da tag `freeze-v1` fixa o repositório inteiro; o manifesto torna
explícito o que é metodológico e permite à readiness recusar uma execução da amostra feita
com qualquer um desses arquivos alterado (fase `experiment`).
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

from shared.timestamps import utc_now_iso

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_FILE = REPO_ROOT / "config" / "freeze_manifest.json"
FREEZE_TAG = "freeze-v1"

FROZEN_GROUPS: dict[str, tuple[str, ...]] = {
    "decision": (
        "config/prompts/decision_prompt_v1.txt",
        "services/orders/app/orchestration/decision_engine.py",
        "services/orders/app/orchestration/rules_engine.py",
        "services/orders/app/orchestration/validator.py",
        "services/orders/app/orchestration/executor.py",
        "services/orders/app/orchestration/state_builder.py",
        "services/orders/app/llm/*.py",
        "shared/decision.py",
        "shared/system_state.py",
        "contracts/*.json",
    ),
    "configuration": (
        "config/experiment_config.yml",
        "config/scenarios/*.yml",
        "config/analysis_plan.yml",
        "config/rabbitmq/*",
        "config/prometheus/*",
    ),
    "data": (
        "datasets/orders_v1.json",
        "datasets/inventory_catalog_v1.json",
    ),
    "analysis": (
        "scripts/analysis/consolidate.py",
        "scripts/analysis/decision_adequacy.py",
        "scripts/analysis/compare_engines.py",
        "scripts/analysis/replay_decisions.py",
    ),
    "environment": (
        "docker-compose.yml",
        "services/orders/Dockerfile",
        "services/inventory/Dockerfile",
        "services/orders/requirements.txt",
        "services/inventory/requirements.txt",
        "requirements-dev.txt",
    ),
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def frozen_files(root: Path = REPO_ROOT, groups: dict[str, tuple[str, ...]] = FROZEN_GROUPS) -> dict[str, dict[str, str]]:
    """Grupo → {caminho relativo: sha256}. Um padrão sem arquivo é erro: o manifesto não pode encolher calado."""
    result: dict[str, dict[str, str]] = {}
    for group, patterns in groups.items():
        files: dict[str, str] = {}
        for pattern in patterns:
            matches = sorted(p for p in root.glob(pattern) if p.is_file())
            if not matches:
                raise FileNotFoundError(f"frozen pattern without files: {pattern}")
            files.update({p.relative_to(root).as_posix(): _sha256(p) for p in matches})
        result[group] = dict(sorted(files.items()))
    return result


def build(root: Path = REPO_ROOT, groups: dict[str, tuple[str, ...]] = FROZEN_GROUPS) -> dict:
    return {"freeze": FREEZE_TAG, "created_at": utc_now_iso(), "groups": frozen_files(root, groups)}


def differences(manifest: dict, root: Path = REPO_ROOT, groups: dict[str, tuple[str, ...]] = FROZEN_GROUPS) -> list[str]:
    """Arquivos alterados, ausentes ou novos em relação ao manifesto; vazio = configuração congelada."""
    expected = {path: digest for files in manifest["groups"].values() for path, digest in files.items()}
    current = {path: digest for files in frozen_files(root, groups).values() for path, digest in files.items()}
    found = [f"changed: {path}" for path in sorted(expected.keys() & current.keys()) if expected[path] != current[path]]
    found += [f"missing: {path}" for path in sorted(expected.keys() - current.keys())]
    found += [f"not in manifest: {path}" for path in sorted(current.keys() - expected.keys())]
    return found


def load(path: Path = MANIFEST_FILE) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true")
    action.add_argument("--write", action="store_true")
    args = parser.parse_args()
    if args.write:
        manifest = build()
        MANIFEST_FILE.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        print(f"{MANIFEST_FILE.relative_to(REPO_ROOT)}: {sum(len(f) for f in manifest['groups'].values())} files")
        return 0
    manifest = load()
    if manifest is None:
        print("no manifest", file=sys.stderr)
        return 1
    found = differences(manifest)
    print("\n".join(found) if found else "frozen: no differences")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
