import tomllib
from pathlib import Path


def test_default_config_is_valid_toml():
    config_path = Path(__file__).resolve().parents[1] / "config.example.toml"

    with config_path.open("rb") as config_file:
        config = tomllib.load(config_file)

    assert config["incremental_overlap_days"] == 7
