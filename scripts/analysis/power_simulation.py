"""Simulação do poder do teste pareado para escolher `N_rep` (D-38; docs/15 §15.7).

    .venv/bin/python -m scripts.analysis.power_simulation

Estima, por Monte Carlo, a probabilidade de o Wilcoxon signed-rank exato bicaudal (o teste de
D-35) rejeitar H0 quando existe um efeito pareado de tamanho `dz` (média das diferenças / desvio-
padrão das diferenças). O limiar é o da 1ª etapa do Holm, `alpha / m`, com `m` = número de métricas
primárias de `config/analysis_plan.yml`: é o caso mais exigente, logo a estimativa é conservadora.

Dois modelos para as diferenças: normal e t de Student com 3 graus de liberdade (caudas pesadas),
padronizada para variância 1. Não usa dado de execução: é planejamento, não resultado da amostra.
Também imprime o menor p bicaudal exato possível para cada número de pares (2 / 2^n).
"""

import argparse
from pathlib import Path

import numpy as np
import yaml
from scipy.stats import wilcoxon

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PLAN = REPO_ROOT / "config" / "analysis_plan.yml"
PAIRS = (8, 10, 12, 15, 20, 25, 30, 40)
EFFECTS = (0.5, 0.8, 1.0, 1.5)
DISTRIBUTIONS = ("normal", "t3")
SEED = 1042
REPLICATIONS = 3000


def holm_first_step_alpha(plan_path: Path) -> tuple[float, int]:
    plan = yaml.safe_load(plan_path.read_text(encoding="utf-8"))
    primaries = [metric for metric in plan["metrics"] if metric["family"] == "primary"]
    return plan["alpha"] / len(primaries), len(primaries)


def differences(rng: np.random.Generator, distribution: str, n: int, dz: float) -> np.ndarray:
    if distribution == "normal":
        noise = rng.standard_normal(n)
    else:
        noise = rng.standard_t(3, n) / np.sqrt(3)  # variância da t(3) = 3
    return dz + noise


def power(rng: np.random.Generator, distribution: str, n: int, dz: float, alpha: float, replications: int) -> float:
    rejections = 0
    for _ in range(replications):
        p_value = wilcoxon(differences(rng, distribution, n, dz), alternative="two-sided", method="exact").pvalue
        rejections += p_value < alpha
    return rejections / replications


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--replications", type=int, default=REPLICATIONS)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()

    alpha, primaries = holm_first_step_alpha(args.plan)
    rng = np.random.default_rng(args.seed)
    print(f"alpha da 1ª etapa do Holm = {alpha:.4f} ({primaries} métricas primárias); "
          f"{args.replications} replicações; seed {args.seed}\n")
    print("| Pares | " + " | ".join(str(n) for n in PAIRS) + " |")
    print("|---|" + "---|" * len(PAIRS))
    print("| Menor p exato | " + " | ".join(f"{2 / 2 ** n:.2g}" for n in PAIRS) + " |")
    for distribution in DISTRIBUTIONS:
        for dz in EFFECTS:
            row = [power(rng, distribution, n, dz, alpha, args.replications) for n in PAIRS]
            print(f"| {distribution}, dz = {dz} | " + " | ".join(f"{value:.2f}" for value in row) + " |")


if __name__ == "__main__":
    main()
