"""Gera o catálogo do Inventory e o dataset de pedidos sintéticos (M6-T02; RNF-005).

    .venv/bin/python -m scripts.datasets.generate_dataset [--check]

Os dois arquivos são versionados e ficam inalterados durante a coleta (metodologia
§4.4.1). A geração é determinística: a mesma seed reconstrói os mesmos bytes, e
`--check` (e o teste de unidade) confirma que os arquivos versionados correspondem à
seed registrada neles.

- `datasets/inventory_catalog_v1.json`: SKUs conhecidos pelo Inventory (D-03).
- `datasets/orders_v1.json`: pedidos com 1 a 3 SKUs distintos do catálogo e
  quantidades de 1 a 5. Só pedidos consistentes: a inconsistência do cenário "dados
  inconsistentes" é aplicada pelo gerador de carga, na janela da falha.
"""

import argparse
import json
import random
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CATALOG_PATH = REPO_ROOT / "datasets" / "inventory_catalog_v1.json"
DATASET_PATH = REPO_ROOT / "datasets" / "orders_v1.json"

CATALOG_ID = "inventory_catalog_v1"
CATALOG_SIZE = 50
DATASET_ID = "orders_v1"
DATASET_SEED = 1042
DATASET_SIZE = 1000
MAX_ITEMS_PER_ORDER = 3
MAX_QUANTITY = 5


def sku(number: int) -> str:
    return f"SKU-{number:03d}"


def build_catalog() -> dict:
    return {"catalog_id": CATALOG_ID, "skus": [sku(n) for n in range(1, CATALOG_SIZE + 1)]}


def build_dataset(catalog: dict, seed: int = DATASET_SEED, size: int = DATASET_SIZE) -> dict:
    rng = random.Random(seed)
    orders = []
    for index in range(1, size + 1):
        skus = sorted(rng.sample(catalog["skus"], rng.randint(1, MAX_ITEMS_PER_ORDER)))
        items = [{"sku": code, "quantity": rng.randint(1, MAX_QUANTITY)} for code in skus]
        orders.append({"index": index, "items": items})
    return {
        "dataset_id": DATASET_ID,
        "seed": seed,
        "generator": "scripts/datasets/generate_dataset.py",
        "catalog": str(CATALOG_PATH.relative_to(REPO_ROOT)),
        "orders": orders,
    }


def render(document: dict) -> str:
    """Um pedido por linha: diffs legíveis e bytes estáveis."""
    if "orders" not in document:
        return json.dumps(document, indent=2) + "\n"
    header = {key: value for key, value in document.items() if key != "orders"}
    lines = [json.dumps(order, separators=(",", ":")) for order in document["orders"]]
    head = json.dumps(header, indent=2)[:-2]  # sem o "}" final
    return head + ',\n  "orders": [\n    ' + ",\n    ".join(lines) + "\n  ]\n}\n"


def expected_files() -> dict[Path, str]:
    catalog = build_catalog()
    return {CATALOG_PATH: render(catalog), DATASET_PATH: render(build_dataset(catalog))}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="só confere os arquivos versionados")
    args = parser.parse_args()
    stale = []
    for path, content in expected_files().items():
        if args.check:
            if not path.is_file() or path.read_text(encoding="utf-8") != content:
                stale.append(path)
        else:
            path.write_text(content, encoding="utf-8")
    for path in stale:
        print(f"desatualizado: {path.relative_to(REPO_ROOT)}", file=sys.stderr)
    return 1 if stale else 0


if __name__ == "__main__":
    sys.exit(main())
