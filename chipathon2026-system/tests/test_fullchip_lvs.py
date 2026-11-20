import json
from pathlib import Path

import pytest

from chipathon2026_integration.errors import ConfigError
from chipathon2026_integration.fullchip_lvs import (
    RepositoryFileNotFound,
    _recreate_data_root,
    download_projects,
    generate_lvs_configs,
    load_repo_list,
    relative_includes,
    resolve_default_branch,
)
from chipathon2026_integration.fullchip_lvs_cli import (
    _format_elapsed,
    _print_download_summary,
)
from chipathon2026_integration.virtual_def import BLOCK_VARIANTS


def _write_inputs(tmp_path: Path, *, conflict: bool = False):
    base = tmp_path / "base.yaml"
    variants = "".join(
        f"  {code}:\n    origin_um: [{variant.origin[0]}, {variant.origin[1]}]\n"
        f"    size_um: [{variant.width}, {variant.height}]\n"
        for code, variant in BLOCK_VARIANTS.items()
    )
    base.write_text(
        "schema_version: 1\nchip:\n  name: base\n  diearea_um: [0, 0, 2935, 2935]\n"
        "io_cells:\n  input_cmos: in\n  input_schmitt: ins\n  bidirectional: bi\n"
        "  bidirectional_24ma: bi24\n  analog: ana\n  power: vdd\n  ground: vss\n"
        "block_variants:\n" + variants,
        encoding="utf-8",
    )
    integration = tmp_path / "integration.yaml"
    integration.write_text(
        "schema_version: 2\nbase_chip: base.yaml\nchip:\n  name: demo\n"
        "  minimum_project_gap_um: 10\nprojects:\n"
        "  - team: A01\n    variant: A\n    quadrant: NW\n"
        "  - team: B02\n    variant: A\n    quadrant: SE\n",
        encoding="utf-8",
    )
    data, integrated, output = tmp_path / "data", tmp_path / "integrated", tmp_path / "lvs"
    integrated.mkdir()
    projects = []
    for team, source_top in (("A01", "core"), ("B02", "B02_core")):
        source = data / team / "verilog" / "top.v"
        source.parent.mkdir(parents=True)
        extra = "module shared(input a); endmodule\n" if conflict else ""
        source.write_text(f"module {source_top}(input a); endmodule\n{extra}", encoding="utf-8")
        project_gds = data / team / "gds" / f"{source_top}.gds"
        project_gds.parent.mkdir()
        project_gds.write_bytes(b"project-gds")
        (data / team / "info.yaml").write_text(
            "project:\n  lvs_config: config/lvs_config.json\n", encoding="utf-8"
        )
        config = data / team / "config/lvs_config.json"
        config.parent.mkdir()
        config.write_text(json.dumps({
            "STD_CELL_LIBRARY": "gf180mcu_fd_sc_mcu7t5v0",
            "TOP_SOURCE": source_top,
            "TOP_LAYOUT": source_top,
            "LAYOUT_FILE": f"$UPRJ_ROOT/gds/{source_top}.gds",
            "LVS_VERILOG_FILES": ["$UPRJ_ROOT/verilog/top.v"],
            "LVS_SPICE_FILES": [""],
        }), encoding="utf-8")
        projects.append({"team": team, "source_top": source_top,
                         "integrated_top": source_top if source_top.startswith(team) else f"{team}_{source_top}"})
    (integrated / "demo_placement_manifest.json").write_text(json.dumps({"projects": projects}), encoding="utf-8")
    (integrated / "demo.gds").write_bytes(b"gds")
    (integrated / "demo.v").write_text("module demo; endmodule\n", encoding="utf-8")
    (integrated / "demo_padring.v").write_text("module demo_padring; endmodule\n", encoding="utf-8")
    lvs_root = tmp_path / "extra_be_checks"
    io = lvs_root / "tech/gf180mcuD/lvs_config.io.json"
    io.parent.mkdir(parents=True)
    io.write_text("{}\n", encoding="utf-8")
    return integration, data, integrated, output, lvs_root


def test_repo_list_is_case_insensitive_and_rejects_duplicates(tmp_path: Path):
    path = tmp_path / "repo.list"
    path.write_text("A01 owner/one\na01 owner/two\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="duplicate team"):
        load_repo_list(path)


def test_relative_netlist_includes_are_rejected():
    assert relative_includes(b'.include "models/local.spice"\n`include "rtl/defs.vh"\n') == [
        "models/local.spice", "rtl/defs.vh"
    ]
    assert relative_includes(b'.include "$PDK_ROOT/models.spice"\n`include "/abs/defs.vh"\n') == []


def test_default_branch_uses_git_symbolic_head(monkeypatch):
    class Result:
        stdout = "ref: refs/heads/trunk\tHEAD\nabc123\tHEAD\n"
    monkeypatch.setattr(
        "chipathon2026_integration.fullchip_lvs.subprocess.run",
        lambda *args, **kwargs: Result(),
    )
    assert resolve_default_branch("owner/repo") == "trunk"


def test_download_summary_lists_failures_and_elapsed_time(capsys, tmp_path: Path):
    manifest = {
        "projects": [{"team": "A01"}],
        "failures": [{"team": "A49", "error": "unsupported relative include"}],
    }
    _print_download_summary(manifest, tmp_path / "data", 3661.234)
    output = capsys.readouterr()
    assert "Downloaded 1 projects" in output.out
    assert "Total download wall-clock time: 01:01:01.234" in output.out
    assert "A49: unsupported relative include" in output.err
    assert _format_elapsed(-1) == "00:00:00.000"


def test_generates_hierarchical_configs_and_renamed_top_wrapper(tmp_path: Path):
    integration, data, integrated, output, lvs_root = _write_inputs(tmp_path)
    manifest = generate_lvs_configs(
        integration, data, integrated, output, lvs_root, tmp_path / "pdks", "gf180mcuD"
    )
    top = json.loads((output / "demo_lvs_config.json").read_text(encoding="utf-8"))
    assert top["INCLUDE_CONFIGS"][0] == "$LVS_ROOT/tech/$PDK/lvs_config.io.json"
    assert top["LAYOUT_FILE"] == "$INTEGRATION_ROOT/demo.gds"
    assert top["A01_ROOT"] == str((data / "A01").resolve())
    assert top["B02_ROOT"] == str((data / "B02").resolve())
    assert top["LVS_VERILOG_FILES"][-2:] == [
        "$INTEGRATION_ROOT/demo_padring.v", "$INTEGRATION_ROOT/demo.v"
    ]
    fragment = json.loads((output / "configs/A01_lvs_config.json").read_text(encoding="utf-8"))
    assert fragment["LVS_VERILOG_FILES"] == ["$A01_ROOT/verilog/top.v"]
    assert "A01_ROOT" not in fragment
    wrapper = output / "wrappers/A01.v"
    assert "module A01_core (a);" in wrapper.read_text(encoding="utf-8")
    assert len(manifest["wrappers"]) == 1
    environment = manifest["environment"]
    assert environment["A01_ROOT"] == str((data / "A01").resolve())
    assert environment["INTEGRATION_ROOT"] == str(integrated.resolve())
    assert "export A01_ROOT=" in (output / "demo_lvs.env").read_text(encoding="utf-8")


def test_cross_project_nonstandard_definition_conflict_is_fatal(tmp_path: Path):
    integration, data, integrated, output, lvs_root = _write_inputs(tmp_path, conflict=True)
    with pytest.raises(ConfigError, match="shared: A01, B02"):
        generate_lvs_configs(
            integration, data, integrated, output, lvs_root, tmp_path / "pdks", "gf180mcuD"
        )


def test_standard_cell_definition_conflict_is_silent(tmp_path: Path):
    integration, data, integrated, output, lvs_root = _write_inputs(tmp_path)
    for source in data.glob("*/verilog/top.v"):
        source.write_text(source.read_text(encoding="utf-8") +
                          "module gf180mcu_fd_sc_mcu7t5v0__inv_1(input A); endmodule\n",
                          encoding="utf-8")
    generate_lvs_configs(
        integration, data, integrated, output, lvs_root, tmp_path / "pdks", "gf180mcuD"
    )


def test_lvs_generation_rejects_nonempty_spice_files_to_fix(tmp_path: Path):
    integration, data, integrated, output, lvs_root = _write_inputs(tmp_path)
    config_path = data / "A01/config/lvs_config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["LVS_SPICE_FILES_TO_FIX"] = ["$UPRJ_ROOT/cdl/fix.spice"]
    config_path.write_text(json.dumps(config), encoding="utf-8")

    with pytest.raises(
        ConfigError, match="LVS_SPICE_FILES_TO_FIX is deprecated and must be empty"
    ):
        generate_lvs_configs(
            integration, data, integrated, output, lvs_root,
            tmp_path / "pdks", "gf180mcuD",
        )


@pytest.mark.parametrize("prefix", ["$UPRJ_ROOT/", "${UPRJ_ROOT}/", "./", ""])
def test_unified_download_preserves_repository_paths_and_replaces_team_trees(tmp_path: Path, monkeypatch, prefix):
    integration, data, _integrated, _output, _lvs_root = _write_inputs(tmp_path)
    (data / "A01/obsolete.txt").write_text("old", encoding="utf-8")
    repo_list = tmp_path / "repo.list"
    repo_list.write_text("A01 owner/a01\nB02 owner/b02\n", encoding="utf-8")
    configs = {
        "owner/a01": {"TOP_LAYOUT": "core", "LAYOUT_FILE": "$UPRJ_ROOT/layout/core.gds",
                      "LVS_VERILOG_FILES": ["$UPRJ_ROOT/netlist/core.v"]},
        "owner/b02": {"TOP_LAYOUT": "B02_core", "LAYOUT_FILE": f"{prefix}layout/B02_core.gds",
                      "LVS_SPICE_FILES": f"{prefix}netlist/B02_core.spice"},
    }

    def fake_get(_session, repo, _ref, path):
        if path == "info.yaml":
            if repo == "owner/a01":
                return (
                    b"project:\n"
                    b"  lvs_config: $UPRJ_ROOT/checks/lvs_config.json\n"
                    b"  export_control: ${UPRJ_ROOT}/docs/export.pdf\n"
                )
            return b"project:\n  lvs_config: ${UPRJ_ROOT}/checks/lvs_config.json\n"
        if path == "checks/lvs_config.json":
            return json.dumps(configs[repo]).encode()
        return f"downloaded:{repo}:{path}\n".encode()

    class Response:
        def raise_for_status(self):
            return None
        def json(self):
            return {"sha": "a" * 40}

    class Session:
        def __init__(self):
            self.headers = {}
        def get(self, _url, timeout=60):
            return Response()

    monkeypatch.setattr("chipathon2026_integration.fullchip_lvs.get_repo_file", fake_get)
    monkeypatch.setattr(
        "chipathon2026_integration.fullchip_lvs.resolve_repo_revision",
        lambda _repo, _ref: "a" * 40,
    )
    manifest = download_projects(
        repo_list, data, assume_yes=True, session=Session()
    )
    assert not (data / "A01/obsolete.txt").exists()
    assert (data / "A01/layout/core.gds").is_file()
    assert (data / "A01/netlist/core.v").is_file()
    assert (data / "A01/checks/lvs_config.json").is_file()
    assert (data / "export_control/A01_export.pdf").read_bytes() == (
        b"downloaded:owner/a01:docs/export.pdf\n"
    )
    assert (data / "B02/netlist/B02_core.spice").is_file()
    assert (data / "B02/layout/B02_core.gds").is_file()
    assert all(item["revision"] == "a" * 40 for item in manifest["projects"])
    a01 = next(item for item in manifest["projects"] if item["team"] == "A01")
    assert a01["top_layout"] == "core"
    assert a01["export_control"]["repository_path"] == "docs/export.pdf"
    b02 = next(item for item in manifest["projects"] if item["team"] == "B02")
    assert b02["export_control"] is None


@pytest.mark.parametrize("missing_export", [False, True])
def test_failed_project_preserves_partial_tree_and_export_document(
    tmp_path: Path, monkeypatch, missing_export,
):
    data = tmp_path / "data"
    repo_list = tmp_path / "repo.list"
    repo_list.write_text("A01 owner/a01\n", encoding="utf-8")

    def fake_get(_session, _repo, _ref, path):
        if path == "info.yaml":
            return (
                b"project:\n"
                b"  export_control: docs/export.pdf\n"
                b"  lvs_config: missing.json\n"
            )
        if path == "docs/export.pdf":
            if missing_export:
                raise RepositoryFileNotFound("docs/export.pdf is missing")
            return b"controlled document"
        raise ConfigError(f"{path} is missing")

    class Session:
        def __init__(self):
            self.headers = {}

    monkeypatch.setattr(
        "chipathon2026_integration.fullchip_lvs.get_repo_file", fake_get
    )
    monkeypatch.setattr(
        "chipathon2026_integration.fullchip_lvs.resolve_repo_revision",
        lambda _repo, _ref: "d" * 40,
    )

    manifest = download_projects(
        repo_list, data, assume_yes=True, session=Session()
    )

    assert manifest["status"] == "partial"
    assert not (data / "A01").exists()
    assert (data / "A01_failed/info.yaml").is_file()
    from chipathon2026_integration.fullchip_lvs_cli import write_download_report
    report = tmp_path / "download.report"
    write_download_report(report, manifest)
    text = report.read_text()
    assert "  info.yaml: info.yaml: " in text
    assert "  lvs_config: NOT FOUND: missing.json" in text
    if missing_export:
        assert "  export_control: NOT FOUND: docs/export.pdf" in text
        assert not manifest["failures"][0]["retained_export_control"]
        return
    assert "  export_control: ../export_control/A01_export.pdf: " in text
    assert (data / "export_control/A01_export.pdf").read_bytes() == (
        b"controlled document"
    )
    assert manifest["failures"][0]["failed_path"] == str(data / "A01_failed")
    assert manifest["failures"][0]["retained_export_control"] == [
        str(data / "export_control/A01_export.pdf")
    ]


def test_download_declines_removal_of_existing_data_root(tmp_path: Path):
    data = tmp_path / "data"
    data.mkdir()
    marker = data / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    repo_list = tmp_path / "repo.list"
    repo_list.write_text("A01 owner/a01\n", encoding="utf-8")

    started = []
    with pytest.raises(ConfigError, match="removal declined"):
        download_projects(
            repo_list, data, input_fn=lambda _prompt: "no",
            start_callback=lambda: started.append(True),
        )

    assert marker.read_text(encoding="utf-8") == "keep"
    assert started == []


def test_recreate_data_root_removes_macos_metadata(tmp_path: Path):
    data = tmp_path / "data"
    data.mkdir()
    (data / ".DS_Store").write_bytes(b"finder metadata")
    (data / "A01").mkdir()
    (data / "A01/partial.gds").write_bytes(b"partial")

    _recreate_data_root(data)

    assert data.is_dir()
    assert list(data.iterdir()) == []
    assert list(tmp_path.glob(".data.chipathon-remove-*")) == []


def test_malformed_info_is_reported_purged_and_other_projects_continue(tmp_path: Path, monkeypatch, capsys):
    data = tmp_path / "data"
    (data / "A01").mkdir(parents=True)
    (data / "A01/old.gds").write_bytes(b"stale")
    repo_list = tmp_path / "repo.list"
    repo_list.write_text("A01 owner/bad\nB02 owner/good\n", encoding="utf-8")

    def fake_get(_session, repo, _ref, path):
        if path == "info.yaml":
            return b"project:\n  lvs_config: good.json\n  io_type : analog\n" if repo == "owner/good" else b"project:\n bad: value\n  broken: yaml\n"
        if path == "good.json":
            return json.dumps({
                "TOP_LAYOUT": "good", "LAYOUT_FILE": "$UPRJ_ROOT/gds/good.gds",
                "LVS_VERILOG_FILES": ["$UPRJ_ROOT/netlist/good.v"],
            }).encode()
        return b"downloaded"

    class Session:
        def __init__(self):
            self.headers = {}
        def get(self, _url, timeout=60):
            raise AssertionError("API fallback was not expected")

    monkeypatch.setattr("chipathon2026_integration.fullchip_lvs.get_repo_file", fake_get)
    monkeypatch.setattr(
        "chipathon2026_integration.fullchip_lvs.resolve_repo_revision",
        lambda _repo, _ref: "b" * 40,
    )
    manifest = download_projects(repo_list, data, assume_yes=True, session=Session())
    output = capsys.readouterr()
    assert "[A01] ERROR: malformed info.yaml" in output.err
    assert "netlist/good.v ->" in output.out
    assert not (data / "A01").exists()
    assert (data / "A01_failed/info.yaml").is_file()
    assert (data / "B02/gds/good.gds").is_file()
    assert manifest["status"] == "partial"
    assert manifest["failures"][0]["team"] == "A01"
    assert manifest["repository_order"] == ["A01", "B02"]


@pytest.mark.parametrize("unsafe", [False, True])
def test_spice_includes_are_downloaded_and_unsafe_paths_fail(
    tmp_path: Path, monkeypatch, capsys, unsafe,
):
    data = tmp_path / "data"
    repo_list = tmp_path / "repo.list"
    repo_list.write_text("A49 owner/bad\nB02 owner/good\n", encoding="utf-8")

    transfers = []

    def fake_get(_session, repo, _ref, path):
        transfers.append((repo, _ref, path))
        if path == "info.yaml":
            return b"project:\n  lvs_config: lvs_config.json\n"
        if path == "lvs_config.json":
            return json.dumps({
                "LAYOUT_FILE": "$UPRJ_ROOT/gds/top.gds",
                "LVS_SPICE_FILES": ["$UPRJ_ROOT/cdl/top.spice"],
            }).encode()
        if path == "cdl/top.spice" and repo == "owner/bad":
            if unsafe:
                return b'.include "../../outside.spice"\n'
            return b'.INCLUDE "child.spice"\n.inc \'../models/device model.spice\'\n'
        if path == "cdl/child.spice":
            return b'.include "top.spice"\n.include "$UPRJ_ROOT/models/shared.spice"\n'
        if path == "models/device model.spice":
            return b'.include "shared.spice"\n'
        return b"downloaded"

    class Session:
        def __init__(self):
            self.headers = {}

    monkeypatch.setattr("chipathon2026_integration.fullchip_lvs.get_repo_file", fake_get)
    monkeypatch.setattr(
        "chipathon2026_integration.fullchip_lvs.resolve_repo_revision",
        lambda _repo, _ref: "c" * 40,
    )
    manifest = download_projects(repo_list, data, assume_yes=True, session=Session())

    output = capsys.readouterr()
    assert (data / "B02/gds/top.gds").is_file()
    if unsafe:
        assert "unsafe repository-relative include in cdl/top.spice" in output.err
        assert not (data / "A49").exists()
        assert (data / "A49_failed/cdl/top.spice").is_file()
        assert [project["team"] for project in manifest["projects"]] == ["B02"]
        assert manifest["failures"][0]["team"] == "A49"
    else:
        assert not manifest["failures"]
        expected = {"cdl/top.spice", "cdl/child.spice", "models/device model.spice", "models/shared.spice"}
        sources = manifest["projects"][0]["source_files"]
        assert {source["repository_path"] for source in sources} == expected
        for path in expected:
            assert (data / "A49" / path).is_file()
            assert transfers.count(("owner/bad", "c" * 40, path)) == 1


def test_download_rejects_nonempty_spice_files_to_fix(
    tmp_path: Path, monkeypatch,
):
    data = tmp_path / "data"
    repo_list = tmp_path / "repo.list"
    repo_list.write_text("A01 owner/a01\n", encoding="utf-8")

    def fake_get(_session, _repo, _ref, path):
        if path == "info.yaml":
            return b"project:\n  lvs_config: lvs_config.json\n"
        if path == "lvs_config.json":
            return json.dumps({
                "LAYOUT_FILE": "$UPRJ_ROOT/gds/top.gds",
                "LVS_SPICE_FILES_TO_FIX": "$UPRJ_ROOT/cdl/fix.spice",
            }).encode()
        raise AssertionError(f"unexpected download: {path}")

    class Session:
        def __init__(self):
            self.headers = {}

    monkeypatch.setattr(
        "chipathon2026_integration.fullchip_lvs.get_repo_file", fake_get
    )
    monkeypatch.setattr(
        "chipathon2026_integration.fullchip_lvs.resolve_repo_revision",
        lambda _repo, _ref: "e" * 40,
    )

    manifest = download_projects(
        repo_list, data, assume_yes=True, session=Session()
    )

    assert manifest["status"] == "partial"
    assert "LVS_SPICE_FILES_TO_FIX is deprecated" in manifest["failures"][0]["error"]
    assert (data / "A01_failed/lvs_config.json").is_file()
    assert not (data / "A01_failed/cdl/fix.spice").exists()


@pytest.mark.parametrize("missing", ["netlist/top.v", "cdl/child.spice", "gds/top.gds"])
def test_download_only_missing_layout_is_fatal(tmp_path, monkeypatch, missing):
    from chipathon2026_integration.fullchip_lvs import Repository, RepositoryFileNotFound, _download_project

    config = {
        "LAYOUT_FILE": "gds/top.gds",
        "LVS_VERILOG_FILES": ["netlist/top.v"],
        "LVS_SPICE_FILES": ["cdl/top.spice"],
    }

    def get_file(_session, _repo, _ref, path):
        if path == missing:
            raise RepositoryFileNotFound(f"{path} not found")
        if path == "info.yaml":
            return b"project:\n  lvs_config: lvs_config.json\n"
        if path == "lvs_config.json":
            return json.dumps(config).encode()
        if path == "cdl/top.spice":
            return b'.include "child.spice"\n'
        return b"data"

    monkeypatch.setattr("chipathon2026_integration.fullchip_lvs.get_repo_file", get_file)
    monkeypatch.setattr("chipathon2026_integration.fullchip_lvs.resolve_repo_revision", lambda *_: "a" * 40)
    if missing == "gds/top.gds":
        with pytest.raises(RepositoryFileNotFound):
            _download_project(None, Repository("A01", "owner/repo"), tmp_path)
    else:
        result = _download_project(None, Repository("A01", "owner/repo"), tmp_path)
        assert Path(result["layout_file"]["local_path"]).is_file()
        assert missing not in {item["repository_path"] for item in result["source_files"]}


def test_missing_export_document_keeps_successful_project(tmp_path, monkeypatch, capsys):
    from chipathon2026_integration.fullchip_lvs import Repository, _download_project
    from chipathon2026_integration.fullchip_lvs_cli import write_download_report

    def get_file(_session, _repo, _ref, path):
        if path == "info.yaml":
            return b"project:\n  lvs_config: lvs_config.json\n  export_control: docs/export.pdf\n"
        if path == "docs/export.pdf":
            raise RepositoryFileNotFound("document missing")
        if path == "lvs_config.json":
            return b'{"LAYOUT_FILE": "top.gds", "TOP_LAYOUT": "top"}'
        return b"gds"

    monkeypatch.setattr("chipathon2026_integration.fullchip_lvs.get_repo_file", get_file)
    monkeypatch.setattr("chipathon2026_integration.fullchip_lvs.resolve_repo_revision", lambda *_: "a" * 40)
    project = _download_project(None, Repository("A01", "owner/repo"), tmp_path)
    assert Path(project["layout_file"]["local_path"]).is_file()
    assert project["export_control"] is None
    assert "WARNING: export_control document not found" in capsys.readouterr().err
    report = tmp_path / "download.report"
    write_download_report(report, {"data_root": str(tmp_path), "projects": [project], "failures": []})
    assert "export_control: NOT FOUND: docs/export.pdf" in report.read_text()
    assert "Successful: 1" in report.read_text()
