from pathlib import Path

import pytest
import yaml

from chipathon2026_integration.errors import ConfigError
from chipathon2026_integration.defparse import DefRect
from chipathon2026_integration.virtual_def import (
    BLOCK_VARIANTS,
    BlockVariant,
    block_size_fits,
    load_block_variants,
    _project_pin_name,
    _project_terminal_name,
    _extend_and_translate,
    generate_project_def,
    micron_to_dbu,
    resolve_layout_text_case,
    select_block_variants,
)


def test_block_table_controls_geometry_and_preserves_decimal_precision(tmp_path):
    table = tmp_path / "blocks.data"
    table.write_text(
        "Blocks\tPin order\tPin count\tPlacement\twidth\theight\tArea\tblock\n"
        "TEST\tW02-W01,N01-N02\t4\t1.25, 2.5\t100.5\t200\t19,999\t0,0-10,20\n"
    )
    variant = load_block_variants(table)["TEST"]
    assert str(variant.width) == "100.5"
    assert tuple(map(str, variant.origin)) == ("1.25", "2.5")
    assert variant.slots == ("W02", "W01", "N01", "N02")
    assert variant.area == 19999
    assert variant.blockages == ((0, 0, 10, 20),)
    original = table.read_text()
    table.write_text(original.replace("\t4\t", "\t5\t"))
    with pytest.raises(ConfigError, match="TEST: Pin count 5 does not match 4 slots"):
        load_block_variants(table)
    table.write_text(original.replace("W02-W01,N01-N02", "W02-W01,W01-W02"))
    with pytest.raises(ConfigError, match="duplicate slots"):
        load_block_variants(table)
    table.write_text(original)
    table.write_text(table.read_text().replace("100.5", "-1"))
    with pytest.raises(ConfigError, match="invalid size"):
        load_block_variants(table)


LEF = r'''
MACRO gf180mcu_fd_io__in_c
  ORIGIN 0 0 ;
  SIZE 10 BY 20 ;
  PIN PU
    DIRECTION INPUT ;
    USE SIGNAL ;
    PORT
      LAYER Metal3 ;
      RECT 1 2 2 3 ;
    END
  END PU
  PIN PD
    DIRECTION INPUT ;
    USE SIGNAL ;
    PORT
      LAYER Metal3 ;
      RECT 3 2 4 3 ;
    END
  END PD
  PIN Y
    DIRECTION OUTPUT ;
    USE SIGNAL ;
    PORT
      LAYER Metal3 ;
      RECT 5 2 6 3 ;
    END
  END Y
END gf180mcu_fd_io__in_c
'''


def setup_files(tmp_path: Path, *, outside: bool = False):
    mapping = {
        "team_code": "T01",
        "pads": [{
            "pin_index": 0, "pin_name": "reset_n", "slot": "W18", "instance": "W18",
            "io_type": "input_cmos", "cell": "gf180mcu_fd_io__in_c",
        }],
    }
    mp = tmp_path / "map.yaml"
    mp.write_text(yaml.safe_dump(mapping), encoding="utf-8")
    bottom = 2034000 if outside else 2040000
    dp = tmp_path / "ring.def"
    dp.write_text(f'''
VERSION 5.8 ;
DESIGN ring ;
UNITS DISTANCE MICRONS 1000 ;
DIEAREA ( 0 0 ) ( 2935000 2935000 ) ;
COMPONENTS 1 ;
- W18 gf180mcu_fd_io__in_c + FIXED ( 0 0 ) N ;
END COMPONENTS
PINS 3 ;
- W18_PU + NET W18_PU + DIRECTION INPUT + USE SIGNAL
  + LAYER Metal3 ( 349500 {bottom} ) ( 350000 2040500 )
  + FIXED ( 0 0 ) N ;
- W18_PD + NET W18_PD + DIRECTION INPUT + USE SIGNAL
  + LAYER Metal3 ( 349500 2041000 ) ( 350000 2041500 )
  + FIXED ( 0 0 ) N ;
- W18_Y + NET W18_Y + DIRECTION OUTPUT + USE SIGNAL
  + LAYER Metal3 ( 349500 2042000 ) ( 350000 2042500 )
  + FIXED ( 0 0 ) N ;
END PINS
END DESIGN
''', encoding="utf-8")
    lp = tmp_path / "in_c.lef"
    lp.write_text(LEF, encoding="utf-8")
    return mp, dp, lp


def test_minimal_variant_selection_preserves_equal_area_placements():
    variants = select_block_variants(
        project_width="500", project_height="500", pin_count=5,
        io_types=["power", "input_cmos", "input_cmos", "input_cmos", "ground"],
    )
    assert [variant.code for variant in variants] == ["D", "EH2", "EV2"]
    assert select_block_variants(
        project_width="1000", project_height="1000", pin_count=21,
        io_types=["input_cmos"] * 21,
    )[0].code == "A"


def test_manual_only_variants_are_never_selected_automatically():
    from chipathon2026_integration.block_data import MANUAL_ONLY_VARIANTS
    assert MANUAL_ONLY_VARIANTS <= set(BLOCK_VARIANTS)
    # each would be the smallest fit for one of these requests if it took part in the selection
    for width, height, pins in (("600", "1050", 8), ("800", "1200", 20), ("1000", "1000", 21)):
        variants = select_block_variants(
            project_width=width, project_height=height, pin_count=pins,
            io_types=["ground"] + ["input_cmos"] * (pins - 1),
        )
        assert not {variant.code for variant in variants} & MANUAL_ONLY_VARIANTS


def test_variant_selection_enforces_endpoint_supply_pads():
    variants = select_block_variants(
        project_width="500", project_height="500", pin_count=5,
        io_types=["input_cmos", "power", "input_cmos", "ground", "input_cmos"],
    )
    assert [variant.code for variant in variants] == ["D", "EH2", "EV2"]


@pytest.mark.parametrize("block_type, expected", [
    ("digital", ["A"]), ("analog", ["A"]),
    ("mixed_signal", ["A"]), ("unknown", ["A"]),
])
def test_a10_selection_requires_dimensions_or_digital_area_headroom(block_type, expected):
    variants = select_block_variants(
        project_width="1039.66", project_height="1020.06", pin_count=8,
        io_types=["ground", "power"] + ["bidirectional_24ma"] * 6,
        block_type=block_type,
    )
    assert [variant.code for variant in variants] == expected


@pytest.mark.parametrize("width,height,block_type,expected", [
    ("10", "10", "digital", True),
    ("20", "4.499", "digital", True),
    ("20", "4.5", "digital", False),
    ("20", "4.501", "digital", False),
    ("20", "4", "analog", False),
])
def test_digital_size_fallback_is_strictly_under_ninety_percent(width, height, block_type, expected):
    from decimal import Decimal
    variant = BlockVariant("TEST", (), (Decimal(0), Decimal(0)), Decimal(10), Decimal(10), 100)
    assert block_size_fits(variant, Decimal(width), Decimal(height), block_type) == expected


def test_digital_selection_still_requires_enough_pin_slots():
    variants = select_block_variants(
        project_width="1039.66", project_height="1020.06", pin_count=11,
        io_types=["input_cmos"] * 11, block_type="digital",
    )
    assert [variant.code for variant in variants] == ["A"]


@pytest.mark.parametrize("width,height,pins,expected", [
    ("600", "1050", 8, ["BV", "CV2"]),
    ("1050", "600", 8, ["BH", "CH2"]),
    ("500", "500", 12, ["D"]),
    ("600", "1000", 17, ["BV"]),
])
def test_corner_alternatives_are_next_larger_fitting_noncorner_blocks(width, height, pins, expected):
    variants = select_block_variants(
        project_width=width, project_height=height, pin_count=pins,
        io_types=["input_cmos"] * pins,
    )
    assert [variant.code for variant in variants] == expected


@pytest.mark.parametrize("width,height,block_type,expected", [
    ("600", "1200", "digital", ["BVE", "BHE", "A"]),
    ("1200", "600", "digital", ["BVE", "BHE", "A"]),
    ("600", "1200", "analog", ["BVE"]),
    ("1200", "600", "analog", ["BHE"]),
])
def test_extended_b_adds_a_only_when_a_fits(width, height, block_type, expected):
    variants = select_block_variants(
        project_width=width, project_height=height, pin_count=22,
        io_types=["input_cmos"] * 22, block_type=block_type,
    )
    assert [variant.code for variant in variants] == expected


def test_micron_conversion_rejects_inexact_values():
    assert micron_to_dbu("1.005", 200, "value") == 201
    with pytest.raises(ConfigError, match="not exactly representable"):
        micron_to_dbu("1.001", 200, "value")


def test_project_terminal_names():
    assert _project_terminal_name("input_cmos", "RST", "Y") == "RST"
    assert _project_terminal_name("input_schmitt", "RST", "PU") == "RST_PU"
    assert _project_terminal_name("bidirectional", "DATA", "Y") == "DATA_IN"
    assert _project_terminal_name("bidirectional", "DATA", "A") == "DATA_OUT"
    assert _project_terminal_name("input_cmos", "DATA[0]", "Y") == "DATA[0]"
    assert _project_terminal_name("input_cmos", "DATA[0]", "PU") == "DATA_PU[0]"
    assert _project_terminal_name("input_cmos", "DATA[0]", "PD") == "DATA_PD[0]"
    assert _project_terminal_name("bidirectional", "data[7]", "Y") == "data_IN[7]"
    assert _project_terminal_name("bidirectional", "data[7]", "A") == "data_OUT[7]"
    assert _project_terminal_name("bidirectional", "data[3][0]", "OE") == "data_OE[3][0]"
    assert _project_terminal_name("bidirectional_24ma", "DATA", "OE") == "DATA_OE"


def test_layout_text_case_resolution_uses_exact_top_cell_spelling():
    assert resolve_layout_text_case(
        ["DATA_OE", "DATA_IE", "DATA_PD[0]"],
        ["DATA_oe", "DATA_oe", "DATA_ie", "DATA_pd[0]", "unrelated"],
    ) == {
        "DATA_OE": "DATA_oe",
        "DATA_IE": "DATA_ie",
        "DATA_PD[0]": "DATA_pd[0]",
    }


def test_layout_text_case_resolution_rejects_conflicting_layout_case():
    with pytest.raises(ConfigError, match="conflicting top-cell layout text"):
        resolve_layout_text_case(["DATA_OE"], ["DATA_OE", "DATA_oe"])


def test_layout_text_case_resolution_rejects_expected_name_collision():
    with pytest.raises(ConfigError, match="case-insensitive collision"):
        resolve_layout_text_case(["DATA_OE", "data_oe"], [])


@pytest.mark.parametrize("name", ["DATA[0", "DATA0]", "DATA[0]bad", "DATA[][0]", "[0]"])
def test_malformed_bus_pin_names_are_rejected(name):
    with pytest.raises(ConfigError, match="bus syntax"):
        _project_pin_name(name, "PU")


def test_project_def_uses_padring_pin_geometry_and_extends_inward(tmp_path):
    mp, dp, lp = setup_files(tmp_path)
    text, metadata = generate_project_def(
        mapping_path=mp, padring_def=dp, lef_paths=[lp], variant_code="D", design_name="T01_D"
    )
    assert "DIEAREA ( 0 0 ) ( 605000 605000 ) ;" in text
    assert "reset_n_PU + NET reset_n_PU + DIRECTION OUTPUT + USE SIGNAL" in text
    assert "- reset_n + NET reset_n + DIRECTION INPUT + USE SIGNAL" in text
    assert "reset_n_Y" not in text
    # The outside portion is omitted and a 1-micron stub begins at local X zero.
    assert "+ LAYER Metal3 ( 0 60000 ) ( 1000 60500 )" in text
    rectangle = metadata["pins"][0]["rectangles"][0]
    assert rectangle["top_level"] == [349500, 2040000, 350000, 2040500]
    assert rectangle["translated_user"] == [0, 60000, 1000, 60500]


def test_project_def_and_metadata_use_layout_text_case(tmp_path):
    mp, dp, lp = setup_files(tmp_path)
    text, metadata = generate_project_def(
        mapping_path=mp, padring_def=dp, lef_paths=[lp], variant_code="D",
        layout_texts=["reset_n_pu"],
    )
    assert "- reset_n_pu + NET reset_n_pu" in text
    pu = next(pin for pin in metadata["pins"] if pin["cell_terminal"] == "PU")
    assert pu["default_project_pin"] == "reset_n_PU"
    assert pu["project_pin"] == "reset_n_pu"


def test_out_of_bounds_geometry_is_clipped(tmp_path):
    mp, dp, lp = setup_files(tmp_path, outside=True)
    _text, metadata = generate_project_def(
        mapping_path=mp, padring_def=dp, lef_paths=[lp], variant_code="D"
    )
    rectangle = metadata["pins"][0]["rectangles"][0]
    assert rectangle["extended_top_level"] == [350000, 2034000, 351000, 2040500]
    assert rectangle["translated_user"] == [0, 54000, 1000, 60500]


def test_fully_outside_rectangle_is_omitted_when_terminal_has_valid_geometry():
    rectangles = _extend_and_translate(
        [
            DefRect("Metal2", -3248, 110000, -1198, 112000),
            DefRect("Metal2", -878, 110000, 1172, 112000),
        ],
        slot="N06", origin=(0, 0), size=(110000, 110000), extension=200,
    )
    assert len(rectangles) == 1
    assert rectangles[0].local == DefRect("Metal2", 0, 109800, 1172, 110000)


def test_ace2_blocks_placement_and_every_routing_layer(tmp_path):
    mp = tmp_path / "map.yaml"
    mp.write_text("pads: []\n", encoding="utf-8")
    dp = tmp_path / "ring.def"
    dp.write_text("VERSION 5.8 ;\nDESIGN ring ;\nUNITS DISTANCE MICRONS 200 ;\nEND DESIGN\n", encoding="utf-8")
    text, metadata = generate_project_def(mapping_path=mp, padring_def=dp, lef_paths=[], variant_code="ACE2")
    assert "BLOCKAGES 12 ;" in text
    assert text.count("- PLACEMENT RECT") == 2
    assert text.count("- LAYER Metal") == 10
    assert metadata["usable_area"] == 4_949_325
    assert metadata["blockages"][1] == [324000, 324000, 447000, 447000]


@pytest.mark.parametrize("variant", ["BV", "BH", "D", "ACV", "ACH", "ACE"])
def test_table_without_metal2_blockages_does_not_invent_them(tmp_path, variant):
    mp = tmp_path / "map.yaml"
    mp.write_text("pads: []\n", encoding="utf-8")
    dp = tmp_path / "ring.def"
    dp.write_text(
        "VERSION 5.8 ;\nDESIGN ring ;\nUNITS DISTANCE MICRONS 200 ;\nEND DESIGN\n",
        encoding="utf-8",
    )
    text, metadata = generate_project_def(
        mapping_path=mp, padring_def=dp, lef_paths=[], variant_code=variant
    )
    assert metadata["metal2_blockages"] == []
    assert text.count("- LAYER Metal2 RECT") == 0


def test_definitive_ace2_slot_order_and_corrected_blockage():
    variant = BLOCK_VARIANTS["ACE2"]
    assert variant.slots[-32:] == tuple(
        [f"E{i:02d}" for i in range(16, 0, -1)] + [f"S{i:02d}" for i in range(22, 6, -1)]
    )
    assert variant.blockages[1] == (1620, 1620, 2235, 2235)


@pytest.mark.parametrize("name", ["Vin-", "Vin+"])
def test_project_def_preserves_differential_pin_signs(tmp_path, name):
    mp, dp, lp = setup_files(tmp_path)
    mapping = yaml.safe_load(mp.read_text())
    mapping["pads"][0]["pin_name"] = name
    mp.write_text(yaml.safe_dump(mapping))
    text, metadata = generate_project_def(
        mapping_path=mp, padring_def=dp, lef_paths=[lp], variant_code="D",
        layout_texts=[name],
    )
    assert f"- {name} + NET {name} + DIRECTION INPUT" in text
    assert name in {pin["project_pin"] for pin in metadata["pins"]}
    assert _project_pin_name(name) == name
    assert _project_pin_name(name, "PU") == f"{name}_PU"
    assert _project_pin_name("Vin-") != _project_pin_name("Vin+")
