import os
from pathlib import Path

from src.runtime_config import load_runtime_profile_env


def test_direct_startup_loads_branch_owned_runtime_profile(monkeypatch, tmp_path):
    monkeypatch.delenv("ARGOS_RUNTIME_ID", raising=False)
    monkeypatch.delenv("ARGOS_PRODUCT_NAME", raising=False)
    monkeypatch.delenv("ARGOS_STORAGE_SLUG", raising=False)
    monkeypatch.delenv("ARGOS_DATA_DIR", raising=False)
    monkeypatch.delenv("ODYSSEUS_DATA_DIR", raising=False)
    monkeypatch.setenv("ARGOS_DATA_ROOT", str(tmp_path))

    load_runtime_profile_env()

    expected_data_dir = str(Path(tmp_path) / "argos-venture")
    assert os.environ.get("ARGOS_RUNTIME_ID") == "argos-venture"
    assert os.environ.get("ARGOS_PRODUCT_NAME") == "Argos Venture"
    assert os.environ.get("ARGOS_STORAGE_SLUG") == "argos-venture"
    assert os.environ.get("ARGOS_DATA_DIR") == expected_data_dir
    assert os.environ.get("ODYSSEUS_DATA_DIR") == expected_data_dir


def test_explicit_runtime_environment_wins(monkeypatch, tmp_path):
    data_dir = str(tmp_path / "nightly")
    monkeypatch.setenv("ARGOS_RUNTIME_ID", "nightly")
    monkeypatch.setenv("ARGOS_PRODUCT_NAME", "Odysseus")
    monkeypatch.setenv("ARGOS_STORAGE_SLUG", "nightly")
    monkeypatch.setenv("ARGOS_DATA_DIR", data_dir)
    monkeypatch.delenv("ODYSSEUS_DATA_DIR", raising=False)

    load_runtime_profile_env()

    assert os.environ.get("ARGOS_RUNTIME_ID") == "nightly"
    assert os.environ.get("ARGOS_PRODUCT_NAME") == "Odysseus"
    assert os.environ.get("ARGOS_STORAGE_SLUG") == "nightly"
    assert os.environ.get("ARGOS_DATA_DIR") == data_dir
    assert os.environ.get("ODYSSEUS_DATA_DIR") == data_dir
