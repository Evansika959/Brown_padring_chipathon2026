import pytest

from chipathon2026_integration.errors import ConfigError
from chipathon2026_integration.info import get_lvs_config_reference, validate_pins


def test_project_lvs_config_only(minimal_info):
    assert get_lvs_config_reference(minimal_info) == "lvs_config.json"
    with pytest.raises(ConfigError):
        get_lvs_config_reference({"lvs_config": "legacy.json", "pins": []})


def test_validate_pin_types_and_analog(minimal_info):
    pins = validate_pins(minimal_info)
    assert [p["io_type"] for p in pins] == [
        "input_schmitt", "bidirectional", "analog", "power", "ground"
    ]
    assert "secondary_esd" not in pins[2]


def test_duplicate_pin_rejected(minimal_info):
    minimal_info["pins"].append({"name": "reset_n", "io_type": "input_cmos"})
    with pytest.raises(ConfigError, match="duplicate"):
        validate_pins(minimal_info)


def test_analog_does_not_require_secondary_esd():
    assert validate_pins({"pins": [{"name": "a", "io_type": "analog"}]}) == [
        {"name": "a", "io_type": "analog"}
    ]


def test_analog_accepts_legacy_secondary_esd_metadata():
    assert validate_pins({
        "pins": [{"name": "a", "io_type": "analog", "secondary_esd": True}]
    })[0]["secondary_esd"] is True


def test_analog_secondary_esd_must_be_boolean_when_present():
    with pytest.raises(ConfigError, match="must be boolean"):
        validate_pins({
            "pins": [{"name": "a", "io_type": "analog", "secondary_esd": "yes"}]
        })


def test_nonanalog_forbids_secondary_esd():
    with pytest.raises(ConfigError, match="prohibited"):
        validate_pins({"pins": [{"name": "d", "io_type": "input_cmos", "secondary_esd": False}]})


def test_empty_pins_rejected():
    with pytest.raises(ConfigError, match="non-empty"):
        validate_pins({"pins": []})
