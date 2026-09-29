"""Leitura do catálogo de SKUs do Inventory e do dataset de pedidos (D-03, M6-T02).

Biblioteca comum: o Inventory carrega o catálogo no seu `inventory.db`; o gerador de
carga lê o dataset. Nenhum dos dois compartilha banco ou estado em execução.
"""

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DatasetOrder:
    index: int
    items: tuple[dict, ...]


@dataclass(frozen=True)
class Dataset:
    dataset_id: str
    seed: int
    orders: tuple[DatasetOrder, ...]


def load_catalog(path: Path) -> tuple[str, ...]:
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    skus = tuple(document["skus"])
    if not skus or len(set(skus)) != len(skus):
        raise ValueError(f"catalog must list distinct SKUs: {path}")
    return skus


def load_dataset(path: Path) -> Dataset:
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    orders = tuple(
        DatasetOrder(index=order["index"], items=tuple(order["items"])) for order in document["orders"]
    )
    if [order.index for order in orders] != list(range(1, len(orders) + 1)):
        raise ValueError(f"dataset order indexes must be 1..N: {path}")
    return Dataset(dataset_id=document["dataset_id"], seed=document["seed"], orders=orders)
