"""Manifesto de hashes da configuração congelada (M8-T04; RNF-027, RNF-028)."""

import pytest

from scripts.experiment.freeze_manifest import MANIFEST_FILE, REPO_ROOT, build, differences, frozen_files, load
from scripts.experiment.readiness import check_freeze

GROUPS = {"decision": ("prompt.txt", "engine/*.py"), "data": ("dataset.json",)}


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "engine").mkdir()
    (tmp_path / "prompt.txt").write_text("PERSONA\n")
    (tmp_path / "engine" / "rules.py").write_text("RULES = 1\n")
    (tmp_path / "dataset.json").write_text("{}\n")
    return tmp_path


def test_manifest_lists_every_frozen_file_by_group(repo):
    manifest = build(repo, GROUPS)

    assert manifest["freeze"] == "freeze-v1"
    assert list(manifest["groups"]["decision"]) == ["engine/rules.py", "prompt.txt"]
    assert all(len(digest) == 64 for files in manifest["groups"].values() for digest in files.values())
    assert differences(manifest, repo, GROUPS) == []


def test_any_change_to_a_frozen_file_is_reported(repo):
    manifest = build(repo, GROUPS)
    (repo / "prompt.txt").write_text("PERSONA alterada\n")
    (repo / "engine" / "new_rule.py").write_text("X = 2\n")

    assert differences(manifest, repo, GROUPS) == ["changed: prompt.txt", "not in manifest: engine/new_rule.py"]

    (repo / "dataset.json").unlink()
    with pytest.raises(FileNotFoundError, match="dataset.json"):
        differences(manifest, repo, GROUPS)  # padrão sem arquivo: o manifesto não encolhe calado


def test_readiness_refuses_an_experiment_run_outside_the_frozen_configuration():
    manifest = {"freeze": "freeze-v1", "groups": {}}

    def changed(_):
        return ["changed: config/prompts/decision_prompt_v1.txt"]

    assert check_freeze("experiment", manifest, find=changed).ok is False
    assert check_freeze("experiment", manifest, find=lambda _: []).ok is True
    pilot = check_freeze("pilot", manifest, find=changed)
    assert pilot.ok is True and pilot.detail["differences"] == ["changed: config/prompts/decision_prompt_v1.txt"]


def test_experiment_phase_without_a_manifest_is_not_ready(monkeypatch):
    from scripts.experiment import readiness

    monkeypatch.setattr(readiness, "load_manifest", lambda: None)

    assert readiness.check_freeze("experiment").ok is False
    assert readiness.check_freeze("pilot").ok is True


def test_the_versioned_manifest_matches_the_repository():
    manifest = load(MANIFEST_FILE)

    assert manifest is not None, "config/freeze_manifest.json ausente"
    assert differences(manifest) == [], "artefato congelado alterado: regrave o manifesto só num novo congelamento"
    assert set(manifest["groups"]) == set(frozen_files(REPO_ROOT))
