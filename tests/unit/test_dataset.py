"""Catálogo e dataset versionados e reprodutíveis pela seed (M6-T02; RNF-005, D-03)."""

from scripts.datasets.generate_dataset import (
    CATALOG_PATH,
    DATASET_PATH,
    DATASET_SEED,
    MAX_ITEMS_PER_ORDER,
    MAX_QUANTITY,
    build_catalog,
    build_dataset,
    expected_files,
)
from shared.catalog import load_catalog, load_dataset


def test_versioned_files_match_the_recorded_seed():
    for path, content in expected_files().items():
        assert path.read_text(encoding="utf-8") == content, path


def test_same_seed_rebuilds_the_same_dataset_and_another_seed_does_not():
    catalog = build_catalog()

    assert build_dataset(catalog, seed=DATASET_SEED) == build_dataset(catalog, seed=DATASET_SEED)
    assert build_dataset(catalog, seed=DATASET_SEED)["orders"] != build_dataset(catalog, seed=7)["orders"]


def test_dataset_orders_only_reference_the_catalog():
    catalog = set(load_catalog(CATALOG_PATH))
    dataset = load_dataset(DATASET_PATH)

    assert dataset.seed == DATASET_SEED
    for order in dataset.orders:
        skus = [item["sku"] for item in order.items]
        assert 1 <= len(skus) <= MAX_ITEMS_PER_ORDER
        assert len(set(skus)) == len(skus) and set(skus) <= catalog
        assert all(1 <= item["quantity"] <= MAX_QUANTITY for item in order.items)
