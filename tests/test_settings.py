from pathlib import Path

import pytest
import yaml

from crawler.settings import SettingsError, load_settings

SETTINGS_PATH = Path(__file__).resolve().parent.parent / "config" / "settings.yaml"


def _write_variant(tmp_path, mutate):
    document = yaml.safe_load(SETTINGS_PATH.read_text(encoding="utf-8"))
    mutate(document)
    target = tmp_path / "settings.yaml"
    target.write_text(yaml.safe_dump(document), encoding="utf-8")
    return target


def test_shipped_settings_load_with_typed_values():
    settings = load_settings(SETTINGS_PATH)

    assert settings.pacing.throttle_multiplier == 2.0
    assert settings.dedup.near_duplicate_action == "flag"
    assert settings.doc_ids.feed == "28591463417151325"


def test_missing_key_names_the_section(tmp_path):
    path = _write_variant(tmp_path, lambda doc: doc["pool"].pop("cooldown_seconds"))

    with pytest.raises(SettingsError, match=r"missing keys under 'pool'.*cooldown_seconds"):
        load_settings(path)


def test_unknown_key_is_rejected(tmp_path):
    path = _write_variant(tmp_path, lambda doc: doc["crawl"].update(typo=1))

    with pytest.raises(SettingsError, match="unknown keys under 'crawl'"):
        load_settings(path)


def test_wrong_type_is_rejected(tmp_path):
    path = _write_variant(tmp_path, lambda doc: doc["transport"].update(max_attempts="five"))

    with pytest.raises(SettingsError, match="transport.max_attempts"):
        load_settings(path)


def test_integer_is_accepted_for_float_field(tmp_path):
    path = _write_variant(tmp_path, lambda doc: doc["pool"].update(cooldown_seconds=30))

    assert load_settings(path).pool.cooldown_seconds == 30.0


def test_invalid_near_duplicate_action_is_rejected(tmp_path):
    path = _write_variant(tmp_path, lambda doc: doc["dedup"].update(near_duplicate_action="ignore"))

    with pytest.raises(SettingsError, match="near_duplicate_action"):
        load_settings(path)
