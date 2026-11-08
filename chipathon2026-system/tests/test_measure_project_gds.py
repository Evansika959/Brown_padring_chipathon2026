import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import gds_project_audit

KLAYOUT = shutil.which("klayout")
pytestmark = pytest.mark.skipif(KLAYOUT is None, reason="KLayout is not installed")


def _write_measurement_inputs(tmp_path: Path, scenario: str, *, missing_boundary=False) -> tuple[Path, Path]:
    gds = tmp_path / f"{scenario}.gds"
    generator = tmp_path / "create_gds.py"
    generator.write_text(
        """
import pya

gds = output_gds
layout = pya.Layout()
layout.dbu = 0.001
top = layout.create_cell("TOP")
outline = layout.layer(0, 0)
poly = layout.layer(30, 0)
diffusion = layout.layer(22, 0)
mim_cap = layout.layer(75, 0)
metal1 = layout.layer(34, 0)
top.shapes(outline).insert(pya.Box(0, 0, 100000, 100000))

def place(name, boxes, x, y):
    cell = layout.create_cell(name)
    for layer, box in boxes:
        cell.shapes(layer).insert(box)
    top.insert(pya.CellInstArray(
        cell.cell_index(), pya.Trans(x, y)
    ))

if scenario == "digital":
    top.shapes(metal1).insert(pya.Box(-900000, -900000, 900000, 900000))
    place("logic", [(poly, pya.Box(0, 0, 10000, 20000))], 10000, 20000)
    place("metal_logic", [(metal1, pya.Box(0, 0, 10000, 20000))], 80000, 20000)
    place("fill", [(poly, pya.Box(0, 0, 100000, 100000))], 200000, 200000)
    place("tap", [(diffusion, pya.Box(0, 0, 100000, 100000))], -200000, -200000)
    place("endcap", [(poly, pya.Box(0, 0, 100000, 100000))], 300000, 300000)
    place("antenna", [(poly, pya.Box(0, 0, 100000, 100000))], -300000, -300000)
    place("tieh", [(poly, pya.Box(0, 0, 100000, 100000))], 400000, 400000)
    place("gf180mcu_fd_sc_mcu7t5v0__buf_1", [(poly, pya.Box(0, 0, 100000, 100000))], -400000, -400000)
    place("gf180mcu_fd_sc_mcu7t5v0__dlya_1", [(poly, pya.Box(0, 0, 100000, 100000))], 500000, 500000)
    place("VIA_Via1", [(metal1, pya.Box(0, 0, 100000, 100000))], 600000, 600000)
elif scenario == "mixed":
    place("logic", [(poly, pya.Box(0, 0, 10000, 20000))], 10000, 20000)
    place("analog_core", [
        (poly, pya.Box(1000, 2000, 3000, 4000)),
        (diffusion, pya.Box(2000, 1000, 5000, 6000)),
        (mim_cap, pya.Box(4000, 3000, 7000, 8000)),
    ], 40000, 40000)
elif scenario == "analog":
    place("analog_core", [
        (poly, pya.Box(1000, 2000, 3000, 4000)),
        (diffusion, pya.Box(2000, 1000, 5000, 6000)),
        (mim_cap, pya.Box(4000, 3000, 7000, 8000)),
    ], 10000, 20000)
    esd_shape = layout.create_cell("esd_guard_geometry")
    esd_shape.shapes(poly).insert(pya.Box(0, 0, 50000, 50000))
    secondary_esd = layout.create_cell("analog_Secondary_ESD_guard")
    secondary_esd.insert(pya.CellInstArray(esd_shape.cell_index(), pya.Trans(0, 0)))
    top.insert(pya.CellInstArray(secondary_esd.cell_index(), pya.Trans(200000, 200000)))
else:
    place("metal_logic", [(metal1, pya.Box(0, 0, 10000, 20000))], 80000, 20000)

layout.write(gds)
""",
        encoding="utf-8",
    )
    if missing_boundary:
        generator.write_text(generator.read_text().replace(
            "top.shapes(outline).insert(pya.Box(0, 0, 100000, 100000))", ""
        ))
    subprocess.run(
        [KLAYOUT, "-b", "-rd", f"scenario={scenario}", "-rd", f"output_gds={gds}", "-r", str(generator)],
        check=True, capture_output=True, text=True,
    )

    lef = tmp_path / "stdcells.lef"
    lef.write_text(
        "MACRO logic\n  CLASS CORE ;\nEND logic\n"
        "MACRO metal_logic\n  CLASS CORE ;\nEND metal_logic\n"
        "MACRO fill\n  CLASS CORE SPACER ;\nEND fill\n"
        "MACRO tap\n  CLASS CORE WELLTAP ;\nEND tap\n"
        "MACRO endcap\n  CLASS ENDCAP PRE ;\nEND endcap\n"
        "MACRO antenna\n  CLASS CORE ANTENNACELL ;\nEND antenna\n"
        "MACRO tieh\n  CLASS CORE TIEHIGH ;\nEND tieh\n"
        "MACRO gf180mcu_fd_sc_mcu7t5v0__buf_1\n  CLASS CORE ;\nEND gf180mcu_fd_sc_mcu7t5v0__buf_1\n"
        "MACRO gf180mcu_fd_sc_mcu7t5v0__dlya_1\n  CLASS CORE ;\nEND gf180mcu_fd_sc_mcu7t5v0__dlya_1\n",
        encoding="utf-8",
    )
    properties = tmp_path / "layers.lyp"
    properties.write_text(
        """<layer-properties>
<properties><name/><source>Poly2 30/0@1</source></properties>
<properties><name/><source>COMP 22/0@1</source></properties>
<properties><name/><source>FuseTop 75/0@1</source></properties>
</layer-properties>
""",
        encoding="utf-8",
    )
    return gds, lef


def _measure(tmp_path: Path, scenario: str) -> dict:
    gds, lef = _write_measurement_inputs(tmp_path, scenario)
    output = tmp_path / "size.json"
    script = Path(__file__).parents[1] / "scripts/measure_project_gds.py"
    subprocess.run(
        [
            KLAYOUT, "-b",
            "-rd", f"input_gds={gds}",
            "-rd", f"output_json={output}",
            "-rd", f"layer_properties={tmp_path / 'layers.lyp'}",
            "-rd", f"std_cell_lefs={lef}",
            "-r", str(script),
        ],
        check=True, capture_output=True, text=True,
    )
    return json.loads(output.read_text(encoding="utf-8"))


def test_digital_measurement_uses_non_tap_non_fill_standard_cells(tmp_path: Path):
    result = _measure(tmp_path, "digital")
    assert result["block_type"] == "digital"
    assert result["width_microns"] == "80"
    assert result["height_microns"] == "20"
    assert result["physical_bbox_microns"] == ["10", "20", "90", "40"]
    assert result["prboundary_microns"] == ["0", "0", "100", "100"]


def test_measurement_does_not_require_a_device_layer_classification(tmp_path: Path):
    result = _measure(tmp_path, "metal_only_standard_cell")
    assert result["block_type"] == "unknown"
    assert result["physical_bbox_microns"] == ["80", "20", "90", "40"]
    assert result["width_microns"] == "10"
    assert result["height_microns"] == "20"


def test_analog_measurement_uses_physical_layers_and_ignores_secondary_esd(
    tmp_path: Path,
):
    result = _measure(tmp_path, "analog")
    assert result["block_type"] == "analog"
    assert result["width_microns"] == "6"
    assert result["height_microns"] == "7"
    assert result["physical_bbox_microns"] == ["11", "21", "17", "28"]


def test_mixed_signal_measurement_combines_standard_and_analog_geometry(tmp_path: Path):
    result = _measure(tmp_path, "mixed")
    assert result["block_type"] == "mixed_signal"
    assert result["physical_bbox_microns"] == ["10", "20", "47", "48"]
    assert result["width_microns"] == "37"
    assert result["height_microns"] == "28"


@pytest.mark.parametrize("missing_boundary", [False, True])
def test_audit_reports_pr_boundary_and_computed_content_sizes(tmp_path: Path, missing_boundary):
    gds, lef = _write_measurement_inputs(tmp_path, "analog", missing_boundary=missing_boundary)
    inspection = gds_project_audit.inspect_gds(
        klayout_exe=KLAYOUT,
        helper_script=Path(__file__).parents[1] / "scripts/klayout_gds_inspect.py",
        gds_path=gds,
        top_cell="TOP",
        layer_properties=tmp_path / "layers.lyp",
        std_cell_lefs=[lef],
    )
    assert (inspection.width_um, inspection.height_um) == ((None, None) if missing_boundary else (100.0, 100.0))
    assert bool(inspection.boundary_error) == missing_boundary
    assert (inspection.computed_width_um, inspection.computed_height_um) == (6.0, 7.0)
    assert inspection.block_type == "analog"
    assert inspection.computed_error is None
