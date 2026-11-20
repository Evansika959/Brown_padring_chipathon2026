#!/usr/bin/env python3
import tempfile
import unittest
from collections import OrderedDict
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import gds_project_audit as audit
from chipathon2026_integration.fullchip_lvs_cli import download_main


class ReportParserTests(unittest.TestCase):
    def test_download_cli_writes_compatible_report(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            manifest = {
                "data_root": str(root / "data"),
                "repository_order": ["A02", "A01", "A03"],
                "projects": [{
                    "team": "A01", "repository": "owner/good", "top_layout": "top",
                    "info": {"local_path": str(root / "data/A01/info.yaml")},
                    "lvs_config": {"local_path": str(root / "data/A01/checks/lvs_config.json")},
                    "layout_file": {"local_path": str(root / "data/A01/gds/top.gds")},
                }],
                "failures": [
                    {"team": "A02", "repository": "owner/bad", "error": "missing file\nsecond line",
                     "info": {"local_path": str(root / "data/A02_failed/info.yaml")}},
                    {"team": "A03", "repository": "owner/missing", "error": "no info"},
                ],
            }

            def download(*args, **kwargs):
                (root / "data").mkdir(exist_ok=True)
                for entry in (*manifest["projects"], *manifest["failures"]):
                    for key in ("info", "lvs_config", "layout_file"):
                        if key in entry:
                            saved = Path(entry[key]["local_path"])
                            saved.parent.mkdir(parents=True, exist_ok=True)
                            saved.write_bytes(b"test file")
                kwargs["start_callback"]()
                return manifest

            for report in (None, root / "reports/custom.report"):
                argv = [str(root / "repo.list"), "--data-root", str(root / "data")]
                if report:
                    argv.extend(["--report", str(report)])
                with patch("chipathon2026_integration.fullchip_lvs_cli.download_projects", side_effect=download):
                    self.assertEqual(download_main(argv), 1)
                records = audit.parse_download_report(report or root / "data/download.report")
                import hashlib
                report_text = (report or root / "data/download.report").read_text()
                self.assertEqual(report_text.count(": " + hashlib.sha256(b"test file").hexdigest()), 4)
                self.assertEqual([record.team for record in records], ["A02", "A01", "A03"])
                self.assertEqual(records[0].info_yaml, "info.yaml")
                self.assertEqual(records[2].info_yaml, "none")
                records = [records[1], records[0]]
                self.assertTrue(records[0].successful)
                self.assertEqual(records[0].top_layout, "top")
                self.assertEqual(records[0].fields["lvs_config"], "checks/lvs_config.json")
                self.assertEqual(records[0].fields["export_control"], "none")
                self.assertEqual(records[0].info_yaml, "info.yaml")
                self.assertEqual(records[0].fields["project_root"], str(root / "data/A01"))
                self.assertEqual(records[0].gds_saved, "gds/top.gds")
                self.assertFalse(records[1].successful)
                self.assertEqual(records[1].fields["ERROR"], "missing file second line")
                self.assertTrue((root / "data/download_manifest.json").is_file())

    def test_report_parser(self):
        text = """\
A01: example/repo
  info.yaml:   info/A01_info.yaml
  TOP_LAYOUT:  top
  GDS saved:   gds/A01/top.gds
A02: example/bad
  ERROR: info.yaml not found
Successful: 1
Failed:     1
"""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "download.report"
            p.write_text(text, encoding="utf-8")
            records = audit.parse_download_report(p)

        self.assertEqual([r.team for r in records], ["A01", "A02"])
        self.assertTrue(records[0].successful)
        self.assertFalse(records[1].successful)
        self.assertEqual(records[0].top_layout, "top")


class PinLoadingTests(unittest.TestCase):
    def test_readiness_flags_require_yaml_booleans(self):
        self.assertEqual(
            audit.readiness_fields({
                "project": {
                    "ws_def_ready": True,
                    "gf_def_ready": False,
                    "drc_ready": "true",
                    "lvs_ready": 1,
                },
            }),
            {
                "ws_def_ready": "Y",
                "gf_def_ready": "N",
                "drc_ready": "?",
                "lvs_ready": "?",
            },
        )

    def test_readiness_flags_ignore_top_level_keys(self):
        self.assertEqual(
            audit.readiness_fields({
                "ws_def_ready": True,
                "gf_def_ready": False,
                "drc_ready": True,
                "lvs_ready": False,
            }),
            {name: "?" for name in audit.READINESS_FLAGS},
        )

    def test_pin_loading_does_not_require_or_split_analog_esd_flag(self):
        text = """\
project:
  pins:
    - name: VDDA
      io_type: power
    - name: VSSA
      io_type: ground
    - name: AIN
      io_type: analog
      secondary_esd: false
    - name: AOUT
      io_type: analog
      secondary_esd: true
    - name: AIN_NO_ESD
      io_type: analog
    - name: CLK
      io_type: input_schmitt
"""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "info.yaml"
            p.write_text(text, encoding="utf-8")
            pins = audit.load_pins(p)

        self.assertEqual(
            [(p.name, p.pad_type) for p in pins],
            [
                ("VDDA", "power"),
                ("VSSA", "ground"),
                ("AIN", "analog"),
                ("AOUT", "analog"),
                ("AIN_NO_ESD", "analog"),
                ("CLK", "input_schmitt"),
            ],
        )

    def test_duplicate_pin_name_is_error(self):
        text = """\
pins:
  - {name: VDD, io_type: power}
  - {name: VDD, io_type: ground}
"""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "info.yaml"
            p.write_text(text, encoding="utf-8")
            with self.assertRaisesRegex(audit.ProjectError, "duplicate pin name"):
                audit.load_pins(p)


class PowerGroundGroupingTests(unittest.TestCase):
    def p(self, name, pad_type):
        return audit.Pin(name, pad_type)

    def test_second_power_starts_new_group(self):
        pins = [
            self.p("VDD0", "power"),
            self.p("DATA0", "bidirectional"),
            self.p("VSS0", "ground"),
            self.p("DATA1", "bidirectional"),
            self.p("VDD1", "power"),
            self.p("AIN1", "analog"),
            self.p("VSS1", "ground"),
        ]
        groups = audit.build_power_ground_groups(pins)
        self.assertEqual(
            [[p.name for p in group.pins] for group in groups],
            [
                ["VDD0", "DATA0", "VSS0", "DATA1"],
                ["VDD1", "AIN1", "VSS1"],
            ],
        )
        self.assertEqual(audit.power_ground_group_errors(pins), [])

    def test_second_ground_starts_new_group(self):
        pins = [
            self.p("VDD0", "power"),
            self.p("SIG0", "input_cmos"),
            self.p("VSS0", "ground"),
            self.p("SIG1", "bidirectional"),
            self.p("VSS1", "ground"),
            self.p("VDD1", "power"),
        ]
        groups = audit.build_power_ground_groups(pins)
        self.assertEqual(
            [[p.name for p in group.pins] for group in groups],
            [["VDD0", "SIG0", "VSS0", "SIG1"], ["VSS1", "VDD1"]],
        )
        self.assertEqual(audit.power_ground_group_errors(pins), [])

    def test_invalid_groups_report_missing_supply(self):
        pins = [
            self.p("VSS0", "ground"),
            self.p("DATA0", "input_cmos"),
            self.p("VSS1", "ground"),  # breaks: group 1 lacks power
            self.p("DATA1", "bidirectional"),
            self.p("VDD1", "power"),
            self.p("VDD2", "power"),  # breaks: group 2 is valid
            self.p("DATA2", "input_schmitt"),  # group 3 lacks ground
        ]
        self.assertEqual(
            audit.power_ground_group_errors(pins),
            [
                "group 1 missing power: VSS0 DATA0",
                "group 3 missing ground: VDD2 DATA2",
            ],
        )

    def test_group_with_no_supply_reports_both(self):
        pins = [
            self.p("DATA0", "input_cmos"),
            self.p("DATA1", "bidirectional"),
        ]
        self.assertEqual(
            audit.power_ground_group_errors(pins),
            ["group 1 missing power+ground: DATA0 DATA1"],
        )


class TextMatchingTests(unittest.TestCase):
    def test_one_to_one_and_input(self):
        pins = [
            audit.Pin("VDDA", "power"),
            audit.Pin("VSSA", "ground"),
            audit.Pin("VSSD", "ground"),
            audit.Pin("AIN", "analog"),
            audit.Pin("CLK", "input_cmos"),
        ]
        texts = OrderedDict(
            [
                ("AIN", 1),
                ("CLK", 1),
                ("EXTRA", 2),
                ("VDDA", 1),
                ("VSSA", 1),
            ]
        )
        result = audit.match_pins_to_text(pins, texts)

        self.assertEqual(result.fields["power"], "1:VDDA")
        self.assertEqual(result.fields["ground"], "2:VSSA *VSSD")
        self.assertEqual(result.fields["analog"], "1:AIN")
        self.assertEqual(result.fields["input_cmos"], "1:CLK")
        self.assertEqual(result.unmatched, "1:EXTRA")
        self.assertEqual(result.errors, ["missing VSSD"])

    def test_bidirectional_in_only_passes(self):
        pins = [audit.Pin("DATA", "bidirectional")]
        texts = OrderedDict([("DATA_IN", 1)])
        result = audit.match_pins_to_text(pins, texts)
        self.assertEqual(result.fields["bidirectional"], "1:DATA")
        self.assertEqual(result.errors, [])
        self.assertEqual(result.unmatched, "0:")

    def test_bidirectional_out_only_passes(self):
        pins = [audit.Pin("DATA", "bidirectional_24ma")]
        texts = OrderedDict([("DATA_OUT", 1)])
        result = audit.match_pins_to_text(pins, texts)
        self.assertEqual(result.fields["bidirectional_24ma"], "1:DATA")
        self.assertEqual(result.errors, [])
        self.assertEqual(result.unmatched, "0:")

    def test_bidirectional_both_pass_and_are_accounted(self):
        pins = [audit.Pin("Data0", "bidirectional")]
        texts = OrderedDict([("Data0_IN", 1), ("Data0_OUT", 1)])
        result = audit.match_pins_to_text(pins, texts)
        self.assertEqual(result.fields["bidirectional"], "1:Data0")
        self.assertEqual(result.errors, [])
        self.assertEqual(result.unmatched, "0:")

    def test_bidirectional_missing_both_is_starred(self):
        pins = [audit.Pin("DATA", "bidirectional")]
        texts = OrderedDict([("DATA_IE", 1)])
        result = audit.match_pins_to_text(pins, texts)
        self.assertEqual(result.fields["bidirectional"], "1:*DATA")
        self.assertEqual(result.errors, ["missing DATA_IN|DATA_OUT"])
        # Optional control text is recognized and not reported as unmatched.
        self.assertEqual(result.unmatched, "0:")

    def test_base_name_case_is_preserved(self):
        pins = [audit.Pin("data0", "bidirectional")]
        texts = OrderedDict([("data0_OUT", 1)])
        result = audit.match_pins_to_text(pins, texts)
        self.assertEqual(result.fields["bidirectional"], "1:data0")
        self.assertEqual(result.errors, [])

    def test_missing_input_has_no_pu_pd_requirement(self):
        pins = [audit.Pin("CLK", "input_schmitt")]
        texts = OrderedDict([("CLK_PU", 1), ("CLK_PD", 1)])
        result = audit.match_pins_to_text(pins, texts)
        self.assertEqual(result.fields["input_schmitt"], "1:*CLK")
        self.assertEqual(result.errors, ["missing CLK"])
        # PU/PD are optional controls, so their presence is recognized.
        self.assertEqual(result.unmatched, "0:")


    def test_optional_bidirectional_control_is_not_unmatched(self):
        pins = [audit.Pin("DATA", "bidirectional")]
        texts = OrderedDict([
            ("DATA_IN", 1),
            ("DATA_IE", 1),
            ("DATA_OE", 1),
            ("DATA_PDRV0", 1),
        ])
        result = audit.match_pins_to_text(pins, texts)
        self.assertEqual(result.fields["bidirectional"], "1:DATA")
        self.assertEqual(result.errors, [])
        self.assertEqual(result.unmatched, "0:")

    def test_indexed_control_is_not_unmatched(self):
        pins = [audit.Pin("DATA", "bidirectional")]
        texts = OrderedDict([
            ("DATA_OUT", 1),
            ("DATA_IE[7]", 1),
            ("DATA_OE[0]", 1),
            ("DATA_PDRV1[12]", 1),
        ])
        result = audit.match_pins_to_text(pins, texts)
        self.assertEqual(result.fields["bidirectional"], "1:DATA")
        self.assertEqual(result.errors, [])
        self.assertEqual(result.unmatched, "0:")

    def test_input_optional_controls_scalar_and_indexed_are_not_unmatched(self):
        pins = [audit.Pin("CLK", "input_cmos")]
        texts = OrderedDict([
            ("CLK", 1),
            ("CLK_PU", 1),
            ("CLK_PD[7]", 1),
        ])
        result = audit.match_pins_to_text(pins, texts)
        self.assertEqual(result.fields["input_cmos"], "1:CLK")
        self.assertEqual(result.errors, [])
        self.assertEqual(result.unmatched, "0:")

    def test_derive_signal_name_scalar(self):
        self.assertEqual(audit.derive_signal_name("DATA", "IN"), "DATA_IN")
        self.assertEqual(audit.derive_signal_name("DATA", "OUT"), "DATA_OUT")

    def test_derive_signal_name_bus_indexed(self):
        self.assertEqual(
            audit.derive_signal_name("qspi_io[0]", "IN"),
            "qspi_io_IN[0]",
        )
        self.assertEqual(
            audit.derive_signal_name("qspi_csn[1]", "OUT"),
            "qspi_csn_OUT[1]",
        )
        self.assertEqual(
            audit.derive_signal_name("qspi_io[3]", "IE"),
            "qspi_io_IE[3]",
        )

    def test_bus_indexed_bidirectional_in_passes(self):
        pins = [audit.Pin("qspi_io[0]", "bidirectional")]
        texts = OrderedDict([
            ("qspi_io_IN[0]", 1),
        ])
        result = audit.match_pins_to_text(pins, texts)
        self.assertEqual(result.fields["bidirectional"], "1:qspi_io[0]")
        self.assertEqual(result.errors, [])
        self.assertEqual(result.unmatched, "0:")

    def test_bus_indexed_bidirectional_out_passes(self):
        pins = [audit.Pin("qspi_csn[1]", "bidirectional")]
        texts = OrderedDict([
            ("qspi_csn_OUT[1]", 1),
        ])
        result = audit.match_pins_to_text(pins, texts)
        self.assertEqual(result.fields["bidirectional"], "1:qspi_csn[1]")
        self.assertEqual(result.errors, [])
        self.assertEqual(result.unmatched, "0:")

    def test_old_append_after_index_data_name_fails(self):
        pins = [audit.Pin("qspi_io[2]", "bidirectional")]
        texts = OrderedDict([
            ("qspi_io[2]_IN", 1),
        ])
        result = audit.match_pins_to_text(pins, texts)
        self.assertEqual(result.fields["bidirectional"], "1:*qspi_io[2]")
        self.assertEqual(
            result.errors,
            ["missing qspi_io_IN[2]|qspi_io_OUT[2]"],
        )
        self.assertEqual(result.unmatched, "1:qspi_io[2]_IN")

    def test_bus_indexed_control_suffix_before_index_is_recognized(self):
        pins = [audit.Pin("qspi_io[3]", "bidirectional")]
        texts = OrderedDict([
            ("qspi_io_IN[3]", 1),
            ("QSPI_IO_ie[3]", 1),
            ("qSpI_iO_PdRv1[3]", 1),
        ])
        result = audit.match_pins_to_text(pins, texts)
        self.assertEqual(result.fields["bidirectional"], "1:qspi_io[3]")
        self.assertEqual(result.errors, [])
        self.assertEqual(result.unmatched, "0:")

    def test_old_append_after_index_control_is_unmatched(self):
        pins = [audit.Pin("qspi_io[0]", "bidirectional")]
        texts = OrderedDict([
            ("qspi_io_IN[0]", 1),
            ("qspi_io[0]_IE", 1),
        ])
        result = audit.match_pins_to_text(pins, texts)
        self.assertEqual(result.errors, [])
        self.assertEqual(result.unmatched, "1:qspi_io[0]_IE")

    def test_control_matching_is_case_insensitive(self):
        pins = [audit.Pin("DATA", "bidirectional")]
        texts = OrderedDict([
            ("DATA_IN", 1),
            ("data_ie", 1),
            ("Data_Oe", 1),
            ("dAtA_pDrV1", 1),
        ])
        result = audit.match_pins_to_text(pins, texts)
        self.assertEqual(result.fields["bidirectional"], "1:DATA")
        self.assertEqual(result.errors, [])
        self.assertEqual(result.unmatched, "0:")

    def test_indexed_control_matching_is_case_insensitive(self):
        pins = [audit.Pin("DATA", "bidirectional")]
        texts = OrderedDict([
            ("DATA_OUT", 1),
            ("data_ie[7]", 1),
            ("DaTa_Oe[0]", 1),
            ("DATA_pdrv0[12]", 1),
        ])
        result = audit.match_pins_to_text(pins, texts)
        self.assertEqual(result.fields["bidirectional"], "1:DATA")
        self.assertEqual(result.errors, [])
        self.assertEqual(result.unmatched, "0:")

    def test_input_control_matching_is_case_insensitive(self):
        pins = [audit.Pin("CLK", "input_cmos")]
        texts = OrderedDict([
            ("CLK", 1),
            ("clk_pu", 1),
            ("ClK_pD[7]", 1),
        ])
        result = audit.match_pins_to_text(pins, texts)
        self.assertEqual(result.fields["input_cmos"], "1:CLK")
        self.assertEqual(result.errors, [])
        self.assertEqual(result.unmatched, "0:")

    def test_data_port_matching_is_case_insensitive(self):
        pins = [audit.Pin("DATA", "bidirectional")]
        texts = OrderedDict([
            ("data_in", 1),
            ("DATA_IE", 1),
        ])
        result = audit.match_pins_to_text(pins, texts)
        self.assertEqual(result.fields["bidirectional"], "1:DATA")
        self.assertEqual(result.errors, [])
        self.assertEqual(result.unmatched, "0:")

    def test_data_out_matching_is_case_insensitive(self):
        pins = [audit.Pin("DATA", "bidirectional")]
        texts = OrderedDict([
            ("DaTa_OuT", 1),
        ])
        result = audit.match_pins_to_text(pins, texts)
        self.assertEqual(result.fields["bidirectional"], "1:DATA")
        self.assertEqual(result.errors, [])
        self.assertEqual(result.unmatched, "0:")

    def test_bus_indexed_data_matching_is_case_insensitive(self):
        pins = [audit.Pin("qspi_io[2]", "bidirectional")]
        texts = OrderedDict([
            ("QSPI_IO_in[2]", 1),
        ])
        result = audit.match_pins_to_text(pins, texts)
        self.assertEqual(result.fields["bidirectional"], "1:qspi_io[2]")
        self.assertEqual(result.errors, [])
        self.assertEqual(result.unmatched, "0:")

    def test_non_numeric_control_index_is_unmatched(self):
        pins = [audit.Pin("DATA", "bidirectional")]
        texts = OrderedDict([
            ("DATA_IN", 1),
            ("DATA_IE[x]", 1),
        ])
        result = audit.match_pins_to_text(pins, texts)
        self.assertEqual(result.unmatched, "1:DATA_IE[x]")

    def test_24ma_pdrv_text_is_not_a_recognized_control(self):
        pins = [audit.Pin("DATA", "bidirectional_24ma")]
        texts = OrderedDict([
            ("DATA_IN", 1),
            ("DATA_PDRV0[7]", 1),
        ])
        result = audit.match_pins_to_text(pins, texts)
        self.assertEqual(result.unmatched, "1:DATA_PDRV0[7]")

    def test_layout_text_containing_dot_is_ignored(self):
        pins = [audit.Pin("CLK", "input_schmitt")]
        texts = OrderedDict([
            ("CLK.debug", 1),
            ("EXTRA.foo", 2),
        ])
        result = audit.match_pins_to_text(pins, texts)
        self.assertEqual(result.fields["input_schmitt"], "1:*CLK")
        self.assertEqual(result.errors, ["missing CLK"])
        self.assertEqual(result.unmatched, "0:")

    def test_dot_text_is_removed_by_filter(self):
        texts = OrderedDict([
            ("KEEP", 1),
            ("ignore.this", 2),
            ("ALSO_KEEP", 3),
        ])
        filtered = audit.filter_layout_text_counts(texts)
        self.assertEqual(
            filtered,
            OrderedDict([("KEEP", 1), ("ALSO_KEEP", 3)]),
        )

    def test_no_inspection_does_not_star_pads(self):
        pins = [
            audit.Pin("CLK", "input_cmos"),
            audit.Pin("DATA", "bidirectional"),
        ]
        result = audit.match_pins_to_text(pins, None)
        self.assertEqual(result.fields["input_cmos"], "1:CLK")
        self.assertEqual(result.fields["bidirectional"], "1:DATA")
        self.assertEqual(result.errors, [])
        self.assertEqual(result.unmatched, "0:")


class MiscTests(unittest.TestCase):
    def test_format_um(self):
        self.assertEqual(audit.format_um(400.5), "400.5")
        self.assertEqual(audit.format_um(500.0), "500.0")
        self.assertEqual(audit.format_um(1.23456789), "1.234568")

    def test_lfs_pointer_detection(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "bad.gds"
            p.write_bytes(
                b"version https://git-lfs.github.com/spec/v1\n"
                b"oid sha256:f464c0b4e7f20d5e0460e5187220a2d50d56821730254f9780213c67b060f2ce\n"
                b"size 54370232\n"
            )
            self.assertTrue(audit.is_git_lfs_pointer_file(p))

            p.write_bytes(b"\x00\x06\x00\x02binary gds-ish content")
            self.assertFalse(audit.is_git_lfs_pointer_file(p))

    def test_csv_header_has_errors(self):
        self.assertEqual(audit.CSV_HEADER[-2:], ("unmatched", "errors"))

    def test_csv_header_reports_both_boundary_and_computed_sizes(self):
        self.assertEqual(
            audit.CSV_HEADER[:7],
            (
                "Team", "Top_cell", "gds_file", "width", "height",
                "computed_width", "computed_height",
            ),
        )

    def test_csv_header_includes_readiness_flags(self):
        self.assertEqual(
            audit.CSV_HEADER[8:12],
            ("ws_def_ready", "gf_def_ready", "drc_ready", "lvs_ready"),
        )

    def test_csv_header_includes_project_type(self):
        self.assertEqual(audit.CSV_HEADER[7], "type")
        self.assertEqual(
            audit.PROJECT_TYPE_LABELS,
            {
                "analog": "Analog",
                "digital": "Digital",
                "mixed_signal": "Mixed signal",
            },
        )


if __name__ == "__main__":
    unittest.main()


def test_audit_reports_missing_configured_files_and_spice_includes(tmp_path):
    import json

    (tmp_path / "top.spice").write_text('.inc "missing.spice"\n.include "top.spice"\n')
    (tmp_path / "top.gds").write_bytes(b"gds")
    (tmp_path / "lvs_config.json").write_text(json.dumps({
        "NET": "$UPRJ_ROOT/missing.v",
        "LVS_VERILOG_FILES": ["$NET", "$PDK_ROOT/external.v"],
        "LVS_SPICE_FILES": ["./top.spice"],
        "LAYOUT_FILE": "top.gds",
    }))
    errors = audit.lvs_file_errors(
        audit.ReportRecord("A01", fields={"project_root": str(tmp_path), "lvs_config": "lvs_config.json"}),
        {}, tmp_path / "info.yaml",
    )
    assert len(errors) == 2
    assert any("LVS_VERILOG_FILES file not found:" in error and "missing.v" in error for error in errors)
    assert any("LVS_SPICE_FILES file not found:" in error and "missing.spice" in error for error in errors)


def test_verilog_pin_names_requiring_escapes_are_audit_errors():
    invalid = ["Vin-", "Vin+", "1pin", "$pin", "a.b", "a/b", "a b", "module", "wire", "a[x]", "a[0]bad"]
    errors = audit.verilog_pin_name_errors([audit.Pin(name, "analog") for name in invalid])
    assert errors == [
        f"pin {name!r} is invalid without a Verilog backslash escape" for name in invalid
    ]
    valid = ["Vin", "vin_p", "_pin", "a$b", "Module", "data[0]", "data[3][0]"]
    assert audit.verilog_pin_name_errors([audit.Pin(name, "analog") for name in valid]) == []


def test_row_reports_invalid_verilog_names_even_when_gds_inspection_fails(tmp_path):
    (tmp_path / "info.yaml").write_text(
        "pins:\n  - {name: 'Vin+', io_type: analog}\n  - {name: 'Vin-', io_type: analog}\n"
    )
    row, _, errors = audit.row_for_project(
        audit.ReportRecord("A52", fields={"info.yaml": "info.yaml"}),
        tmp_path, "klayout", tmp_path / "helper.py", True, tmp_path / "layers.lyp", [],
    )
    for name in ["Vin+", "Vin-"]:
        message = f"pin {name!r} is invalid without a Verilog backslash escape"
        assert message in errors
        assert message in row["errors"]
