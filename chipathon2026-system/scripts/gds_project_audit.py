#!/usr/bin/env python3
"""Audit Chipathon GDS sizes and top-level I/O text against info.yaml.

Implements gds_project_size_io_audit_spec_v9.md.

The main program uses ordinary Python + PyYAML. GDS inspection is delegated to
klayout_gds_inspect.py, which is executed by KLayout in batch mode.

Typical use:
    python3 gds_project_audit.py download.report -o gds_project_audit.csv

If KLayout is not in PATH:
    python3 gds_project_audit.py download.report \
        --klayout /Applications/klayout.app/Contents/MacOS/klayout
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from typing import Any, Iterable

try:
    import yaml
except ImportError as exc:
    raise SystemExit(
        "ERROR: PyYAML is required. Install it with: python3 -m pip install pyyaml"
    ) from exc


PAD_TYPES = (
    "power",
    "ground",
    "analog",
    "input_cmos",
    "input_schmitt",
    "bidirectional",
    "bidirectional_24ma",
)

ONE_TO_ONE_TYPES = {
    "power",
    "ground",
    "analog",
}

INPUT_TYPES = {
    "input_cmos",
    "input_schmitt",
}

BIDIRECTIONAL_TYPES = {
    "bidirectional",
    "bidirectional_24ma",
}

INPUT_CONTROL_SIGNALS = (
    "PU",
    "PD",
)

BIDIRECTIONAL_CONTROL_SIGNALS = (
    "IE",
    "OE",
    "PU",
    "PD",
    "CS",
    "SL",
    "PDRV0",
    "PDRV1",
)

BIDIRECTIONAL_24MA_CONTROL_SIGNALS = (
    "IE",
    "OE",
    "PU",
    "PD",
    "CS",
    "SL",
)

CSV_HEADER = (
    "Team",
    "Top_cell",
    "gds_file",
    "width",
    "height",
    "computed_width",
    "computed_height",
    "type",
    "ws_def_ready",
    "gf_def_ready",
    "drc_ready",
    "lvs_ready",
    *PAD_TYPES,
    "unmatched",
    "errors",
)

# Team records in the current report use A01/B17/etc. Restricting the pattern
# prevents summary lines such as "Successful: 44" from becoming records.
RECORD_RE = re.compile(r"^([A-Za-z]+\d+):\s*(.*)$")
FIELD_RE = re.compile(r"^\s+([^:]+):\s*(.*)$")
LFS_VERSION_LINE = b"version https://git-lfs.github.com/spec/v1"
READINESS_FLAGS = ("ws_def_ready", "gf_def_ready", "drc_ready", "lvs_ready")
PROJECT_TYPE_LABELS = {
    "analog": "Analog",
    "digital": "Digital",
    "mixed_signal": "Mixed signal",
}


@dataclass
class ReportRecord:
    team: str
    repository: str = ""
    fields: dict[str, str] = field(default_factory=dict)

    @property
    def successful(self) -> bool:
        return bool(self.fields.get("GDS saved"))

    @property
    def info_yaml(self) -> str:
        return self.fields.get("info.yaml", "")

    @property
    def top_layout(self) -> str:
        return self.fields.get("TOP_LAYOUT", "")

    @property
    def gds_saved(self) -> str:
        return self.fields.get("GDS saved", "")


@dataclass(frozen=True)
class Pin:
    name: str
    pad_type: str


@dataclass
class PinGroup:
    number: int
    pins: list[Pin]
    has_power: bool
    has_ground: bool


@dataclass
class GdsInspection:
    width_um: float | None
    height_um: float | None
    text_counts: OrderedDict[str, int]
    boundary_bbox_um: list[float] | None = None
    computed_width_um: float | None = None
    computed_height_um: float | None = None
    block_type: str | None = None
    computed_error: str | None = None
    boundary_error: str | None = None


@dataclass
class MatchResult:
    fields: dict[str, str]
    unmatched: str
    errors: list[str]


class ProjectError(Exception):
    """An error associated with one project rather than the whole run."""


def parse_download_report(path: Path) -> list[ReportRecord]:
    """Parse project records from a downloader report, preserving order."""
    records: list[ReportRecord] = []
    current: ReportRecord | None = None
    data_root: Path | None = None

    with path.open("r", encoding="utf-8", errors="replace") as f:
        for raw_line in f:
            line = raw_line.rstrip("\r\n")
            if line.startswith("Data root: "):
                data_root = resolve_report_path(line.removeprefix("Data root: "), path.resolve().parent)
                continue

            match = RECORD_RE.match(line)
            if match:
                if current is not None:
                    records.append(current)
                current = ReportRecord(
                    team=match.group(1),
                    repository=match.group(2).strip(),
                )
                if data_root is not None:
                    current.fields["project_root"] = str(data_root / current.team)
                continue

            if current is None:
                continue

            match = FIELD_RE.match(line)
            if match:
                key, value = match.group(1).strip(), match.group(2).strip()
                if key in {"info.yaml", "lvs_config", "export_control", "GDS saved"}:
                    value = re.sub(r":\s*[0-9a-fA-F]{64}$", "", value)
                current.fields[key] = value

    if current is not None:
        records.append(current)

    return records


def resolve_report_path(value: str, base_dir: Path) -> Path:
    p = Path(os.path.expanduser(value))
    if not p.is_absolute():
        p = base_dir / p
    return p.resolve()


def _normalize_token(value: str) -> str:
    s = value.strip().lower()
    s = s.replace("→", "->")
    s = re.sub(r"[\s\-]+", "_", s)
    s = re.sub(r"_+", "_", s)
    return s.strip("_")


def _find_pin_list(data: Any) -> list[Any]:
    """Find the ordered pin list in supported info.yaml locations."""
    if not isinstance(data, dict):
        raise ProjectError("info.yaml top level is not a mapping")

    candidate_paths = (
        ("pins",),
        ("project", "pins"),
        ("io", "pins"),
        ("project", "io", "pins"),
        ("padframe", "pins"),
        ("project", "padframe", "pins"),
    )

    for keys in candidate_paths:
        obj: Any = data
        ok = True
        for key in keys:
            if not isinstance(obj, dict) or key not in obj:
                ok = False
                break
            obj = obj[key]
        if ok:
            if not isinstance(obj, list):
                raise ProjectError(
                    f"info.yaml field {'.'.join(keys)!r} exists but is not a list"
                )
            return obj

    raise ProjectError(
        "info.yaml does not contain a pin list "
        "(expected 'pins' or 'project.pins')"
    )


def _pin_name(entry: dict[str, Any], index: int) -> str:
    for key in ("name", "pin", "port", "signal"):
        value = entry.get(key)
        if value is not None:
            name = str(value).strip()
            if name:
                return name
    raise ProjectError(f"pin entry {index} does not contain a valid name")


def _raw_pin_type(entry: dict[str, Any], index: int) -> str:
    for key in ("io_type", "pad_type", "type"):
        value = entry.get(key)
        if value is not None:
            text = str(value).strip()
            if text:
                return text
    raise ProjectError(f"pin entry {index} does not contain an io_type/pad_type")


def normalize_pad_type(entry: dict[str, Any], index: int) -> str:
    """Map supported info.yaml spellings to the report's canonical pad types."""
    raw = _raw_pin_type(entry, index)
    raw_lower = raw.strip().lower()

    # Legacy audit labels remain accepted but map to the single analog category.
    compact = re.sub(r"\s+", "", raw_lower)
    if compact in {
        "analog+esd",
        "analog_with_esd",
        "analogwithesd",
        "analog_secondary_esd",
        "analog-esd",
        "analog_without_esd",
        "analogwithoutesd",
        "analog_no_esd",
    }:
        return "analog"

    token = _normalize_token(raw)

    aliases = {
        "power": {"power", "vdd", "supply", "power_pad"},
        "ground": {"ground", "gnd", "vss", "ground_pad"},
        "input_cmos": {
            "input_cmos",
            "cmos_input",
            "digital_input",
            "digital_input_cmos",
            "input",
        },
        "input_schmitt": {
            "input_schmitt",
            "schmitt_input",
            "digital_input_schmitt",
            "schmitt",
            "schmitt_trigger_input",
        },
        "bidirectional": {
            "bidirectional",
            "bidir",
            "bidirectional_io",
            "bidir_io",
            "io_bidirectional",
        },
        "bidirectional_24ma": {
            "bidirectional_24ma",
            "bidir_24ma",
            "bidirectional24ma",
            "bidir24ma",
            "bidirectional_24_ma",
            "bidir_24_ma",
        },
    }

    for canonical, values in aliases.items():
        if token in values:
            return canonical

    if token in {
        "analog",
        "analog_io",
        "analog_input",
        "analog_output",
        "asig",
        "analog_pad",
    }:
        return "analog"

    if raw in PAD_TYPES:
        return raw

    raise ProjectError(
        f"pin entry {index} ({_pin_name(entry, index)!r}) "
        f"has unsupported pad type {raw!r}"
    )


def load_info_data(info_path: Path) -> dict[str, Any]:
    if not info_path.is_file():
        raise ProjectError(f"info.yaml not found: {info_path}")

    try:
        with info_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception as exc:
        raise ProjectError(f"cannot parse {info_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ProjectError("info.yaml top level is not a mapping")
    return data


def readiness_fields(data: dict[str, Any]) -> dict[str, str]:
    def marker(value: Any) -> str:
        if value is True:
            return "Y"
        if value is False:
            return "N"
        return "?"

    project = data.get("project")
    project = project if isinstance(project, dict) else {}
    return {name: marker(project.get(name)) for name in READINESS_FLAGS}


def pins_from_info_data(data: dict[str, Any]) -> list[Pin]:
    """Normalize pins while preserving info.yaml order."""

    entries = _find_pin_list(data)
    pins: list[Pin] = []
    seen: set[str] = set()

    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise ProjectError(f"pin entry {index} is not a mapping")

        name = _pin_name(entry, index)
        if name in seen:
            raise ProjectError(f"duplicate pin name in info.yaml: {name}")
        seen.add(name)

        pad_type = normalize_pad_type(entry, index)
        pins.append(Pin(name=name, pad_type=pad_type))

    return pins


def load_pins(info_path: Path) -> list[Pin]:
    """Load and normalize pins while preserving info.yaml order."""
    return pins_from_info_data(load_info_data(info_path))


def build_power_ground_groups(pins: list[Pin]) -> list[PinGroup]:
    """Split ordered pins when a second power or second ground is encountered."""
    groups: list[PinGroup] = []
    current: list[Pin] = []
    has_power = False
    has_ground = False

    def finish_current() -> None:
        nonlocal current, has_power, has_ground
        if current:
            groups.append(
                PinGroup(
                    number=len(groups) + 1,
                    pins=current,
                    has_power=has_power,
                    has_ground=has_ground,
                )
            )
        current = []
        has_power = False
        has_ground = False

    for pin in pins:
        duplicate_supply = (
            (pin.pad_type == "power" and has_power)
            or (pin.pad_type == "ground" and has_ground)
        )
        if duplicate_supply:
            finish_current()

        current.append(pin)
        if pin.pad_type == "power":
            has_power = True
        elif pin.pad_type == "ground":
            has_ground = True

    finish_current()
    return groups


def power_ground_group_errors(pins: list[Pin]) -> list[str]:
    """Return errors for groups that lack power, ground, or both."""
    errors: list[str] = []
    for group in build_power_ground_groups(pins):
        if group.has_power and group.has_ground:
            continue

        if not group.has_power and not group.has_ground:
            missing = "power+ground"
        elif not group.has_power:
            missing = "power"
        else:
            missing = "ground"

        names = " ".join(pin.name for pin in group.pins)
        errors.append(f"group {group.number} missing {missing}: {names}")

    return errors


def is_git_lfs_pointer_file(path: Path) -> bool:
    """Return True when a local 'GDS' is still a Git LFS pointer file."""
    if not path.is_file():
        return False
    try:
        with path.open("rb") as f:
            head = f.read(1024)
    except OSError:
        return False

    # LFS pointers are small UTF-8 text files beginning with this exact line.
    return head.startswith(LFS_VERSION_LINE)


def inspect_gds(
    klayout_exe: str,
    helper_script: Path,
    gds_path: Path,
    top_cell: str,
    layer_properties: Path,
    std_cell_lefs: list[Path],
) -> GdsInspection:
    if not gds_path.is_file():
        raise ProjectError(f"GDS file not found: {gds_path}")
    if is_git_lfs_pointer_file(gds_path):
        raise ProjectError(f"unresolved Git LFS pointer: {gds_path}")
    if not top_cell:
        raise ProjectError("TOP_LAYOUT is missing from download report")
    if not helper_script.is_file():
        raise ProjectError(f"KLayout helper script not found: {helper_script}")

    with tempfile.TemporaryDirectory(prefix="gds_project_audit_") as tmp:
        result_path = Path(tmp) / "inspection.json"

        cmd = [
            klayout_exe,
            "-b",
            "-r",
            str(helper_script),
            "-rd",
            f"gds_file={gds_path}",
            "-rd",
            f"top_cell={top_cell}",
            "-rd",
            f"output_json={result_path}",
            "-rd",
            f"layer_properties={layer_properties}",
            "-rd",
            f"std_cell_lefs_json={json.dumps([str(path) for path in std_cell_lefs])}",
        ]

        try:
            completed = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
        except FileNotFoundError as exc:
            raise ProjectError(
                f"KLayout executable not found: {klayout_exe!r}; "
                "use --klayout to specify it"
            ) from exc

        if not result_path.is_file():
            detail = completed.stderr.strip() or completed.stdout.strip()
            if detail:
                detail = f": {detail}"
            raise ProjectError(
                "KLayout inspection did not produce a result file "
                f"(exit {completed.returncode}){detail}"
            )

        try:
            payload = json.loads(result_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ProjectError(f"invalid KLayout helper output: {exc}") from exc

        if not payload.get("ok", False):
            raise ProjectError(payload.get("error", "unknown KLayout inspection error"))

        if completed.returncode != 0:
            raise ProjectError(
                f"KLayout returned exit code {completed.returncode} "
                "even though an output file was produced"
            )

        counts: OrderedDict[str, int] = OrderedDict()
        for item in payload.get("text_counts", []):
            counts[str(item["text"])] = int(item["count"])

        return GdsInspection(
            width_um=payload.get("width_um"),
            height_um=payload.get("height_um"),
            text_counts=counts,
            boundary_bbox_um=payload.get("boundary_bbox_um"),
            computed_width_um=payload.get("computed_width_um"),
            computed_height_um=payload.get("computed_height_um"),
            block_type=payload.get("block_type"),
            computed_error=payload.get("computed_error"),
            boundary_error=payload.get("boundary_error"),
        )


def format_um(value: float | None) -> str:
    if value is None:
        return ""
    s = f"{float(value):.6f}".rstrip("0").rstrip(".")
    if "." not in s:
        s += ".0"
    return s


def format_name_field(names: Iterable[str]) -> str:
    names_list = list(names)
    if not names_list:
        return "0:"
    return f"{len(names_list)}:" + " ".join(names_list)


TRAILING_BUS_INDEX_RE = re.compile(r"^(?P<stem>.+)(?P<index>\[[0-9]+\])$")


def split_trailing_bus_index(name: str) -> tuple[str, str | None]:
    """Split a final decimal bus index from a configured pin name.

    Examples:
        DATA       -> ("DATA", None)
        qspi_io[0] -> ("qspi_io", "[0]")
    """
    match = TRAILING_BUS_INDEX_RE.fullmatch(name)
    if match is None:
        return name, None
    return match.group("stem"), match.group("index")


def derive_signal_name(pin_name: str, suffix: str) -> str:
    """Insert an uppercase-style suffix before a trailing bus index.

    Examples:
        derive_signal_name("DATA", "IN")       -> "DATA_IN"
        derive_signal_name("qspi_io[0]", "IN") -> "qspi_io_IN[0]"
        derive_signal_name("qspi_io[3]", "OE") -> "qspi_io_OE[3]"
    """
    stem, index = split_trailing_bus_index(pin_name)
    if index is None:
        return f"{stem}_{suffix}"
    return f"{stem}_{suffix}{index}"


def control_signals_for_pin(pin: Pin) -> tuple[str, ...]:
    """Return optional I/O control suffixes recognized for a configured pad."""
    if pin.pad_type in INPUT_TYPES:
        return INPUT_CONTROL_SIGNALS
    if pin.pad_type == "bidirectional":
        return BIDIRECTIONAL_CONTROL_SIGNALS
    if pin.pad_type == "bidirectional_24ma":
        return BIDIRECTIONAL_24MA_CONTROL_SIGNALS
    return ()


def is_recognized_control_text(text: str, pins: list[Pin]) -> bool:
    """Return True for optional control text belonging to a configured pad.

    Control-text matching is case-insensitive across the complete derived
    control name.

    If the configured pad already ends in a bus index, the control suffix is
    inserted before that index:

        qspi_io[0] -> qspi_io_IE[0]

    For scalar configured pad names, an additional decimal control index is
    also accepted:

        DATA_IE
        DATA_IE[0]
        DATA_IE[7]

    Recognizing a control only prevents it from being reported as unmatched.
    Required data-port matching remains case-sensitive.
    """
    text_folded = text.casefold()

    for pin in pins:
        _, pin_index = split_trailing_bus_index(pin.name)

        for control in control_signals_for_pin(pin):
            canonical = derive_signal_name(pin.name, control)
            canonical_folded = canonical.casefold()

            if text_folded == canonical_folded:
                return True

            # The extra [index] form is only valid when the configured pad is
            # scalar.  A bus-indexed configured pad already carries its own
            # index in the canonical derived name.
            if pin_index is None:
                prefix_folded = canonical_folded + "["
                if text_folded.startswith(prefix_folded) and text.endswith("]"):
                    index = text[len(canonical) + 1:-1]
                    if index.isdigit():
                        return True

    return False


def find_case_insensitive_name(
    expected: str,
    text_names: set[str],
) -> str | None:
    """Return the actual matching layout-text name, ignoring case."""
    expected_folded = expected.casefold()
    for text in text_names:
        if text.casefold() == expected_folded:
            return text
    return None


def filter_layout_text_counts(
    text_counts: OrderedDict[str, int] | None,
) -> OrderedDict[str, int] | None:
    """Remove layout text that the audit specification says to ignore.

    Any text containing '.' is excluded entirely: it cannot satisfy a required
    port and is not reported as unmatched.
    """
    if text_counts is None:
        return None

    return OrderedDict(
        (text, count)
        for text, count in text_counts.items()
        if "." not in text
    )


def match_pins_to_text(
    pins: list[Pin],
    text_counts: OrderedDict[str, int] | None,
) -> MatchResult:
    """Apply v5 data-port matching rules and build compact CSV fields.

    If text_counts is None, layout matching was unavailable. The configured
    pads are still listed, but no '*' is added because absence was not proven.
    """
    text_counts = filter_layout_text_counts(text_counts)
    text_names = set(text_counts.keys()) if text_counts is not None else None
    by_type: dict[str, list[str]] = {pad_type: [] for pad_type in PAD_TYPES}
    expected_names: set[str] = set()
    errors: list[str] = []

    for pin in pins:
        display_name = pin.name
        missing = False

        if pin.pad_type in ONE_TO_ONE_TYPES or pin.pad_type in INPUT_TYPES:
            expected_names.add(pin.name)
            if text_names is not None and pin.name not in text_names:
                missing = True
                errors.append(f"missing {pin.name}")

        elif pin.pad_type in BIDIRECTIONAL_TYPES:
            in_name = derive_signal_name(pin.name, "IN")
            out_name = derive_signal_name(pin.name, "OUT")

            if text_names is not None:
                in_match = find_case_insensitive_name(in_name, text_names)
                out_match = find_case_insensitive_name(out_name, text_names)

                # Account for the actual layout-text spelling/case that matched.
                if in_match is not None:
                    expected_names.add(in_match)
                if out_match is not None:
                    expected_names.add(out_match)
                if pin.name in text_names:
                    expected_names.add(pin.name)

                if in_match is None and out_match is None and pin.name not in text_names:
                    missing = True
                    errors.append(f"missing {in_name}|{out_name}")
            else:
                # Without layout text, keep the canonical derived names as the
                # expected set for deterministic reporting behavior.
                expected_names.add(in_name)
                expected_names.add(out_name)

        else:  # Defensive: load_pins should already prevent this.
            raise ProjectError(f"unsupported canonical pad type: {pin.pad_type}")

        if missing:
            display_name = "*" + display_name
        by_type[pin.pad_type].append(display_name)

    fields = {
        pad_type: format_name_field(by_type[pad_type])
        for pad_type in PAD_TYPES
    }

    if text_names is None:
        unmatched = "0:"
    else:
        optional_control_names = {
            text
            for text in text_names
            if is_recognized_control_text(text, pins)
        }
        unmatched_names = sorted(
            text_names - expected_names - optional_control_names
        )
        unmatched = format_name_field(unmatched_names)

    return MatchResult(fields=fields, unmatched=unmatched, errors=errors)


def print_inspection_diagnostics(
    team: str,
    top_cell: str,
    inspection: GdsInspection,
    quiet_text: bool,
) -> None:
    print(
        f"{team}: {top_cell} PR-boundary size "
        f"{format_um(inspection.width_um)} x "
        f"{format_um(inspection.height_um)} um"
    )
    print(
        f"{team}: {inspection.block_type or 'computed'} content size "
        f"{format_um(inspection.computed_width_um)} x "
        f"{format_um(inspection.computed_height_um)} um"
    )

    if quiet_text:
        return

    print(f"{team}: top-level text")
    if not inspection.text_counts:
        print("  (none)")
        return
    for text, count in inspection.text_counts.items():
        print(f"  {count:5d}  {text}")


def row_for_project(
    record: ReportRecord,
    base_dir: Path,
    klayout_exe: str,
    helper_script: Path,
    quiet_text: bool,
    layer_properties: Path,
    std_cell_lefs: list[Path],
) -> tuple[dict[str, str], Counter[str], list[str]]:
    project_errors: list[str] = []
    pins: list[Pin] = []
    readiness = {name: "?" for name in READINESS_FLAGS}
    inspection: GdsInspection | None = None

    info_path = (
        resolve_report_path(record.info_yaml, Path(record.fields.get("project_root", base_dir)))
        if record.info_yaml
        else None
    )
    gds_path = (
        resolve_report_path(record.gds_saved, Path(record.fields.get("project_root", base_dir)))
        if record.gds_saved
        else None
    )

    if info_path is None:
        project_errors.append("info.yaml path missing from successful report record")
    else:
        try:
            info_data = load_info_data(info_path)
            readiness = readiness_fields(info_data)
            pins = pins_from_info_data(info_data)
            project_errors.extend(power_ground_group_errors(pins))
        except ProjectError as exc:
            project_errors.append(str(exc))

    if gds_path is None:
        project_errors.append("GDS saved path missing from successful report record")
    else:
        try:
            inspection = inspect_gds(
                klayout_exe=klayout_exe,
                helper_script=helper_script,
                gds_path=gds_path,
                top_cell=record.top_layout,
                layer_properties=layer_properties,
                std_cell_lefs=std_cell_lefs,
            )
            if inspection.boundary_error:
                print(f"{record.team}: ERROR: {inspection.boundary_error}", file=sys.stderr)
            if inspection.computed_error:
                project_errors.append(
                    f"computed content size unavailable: {inspection.computed_error}"
                )
        except ProjectError as exc:
            project_errors.append(str(exc))

    if inspection is None:
        project_errors.append(
            "computed content size unavailable: project GDS inspection failed"
        )
    elif not inspection.computed_error and (
        inspection.computed_width_um is None or inspection.computed_height_um is None
    ):
        project_errors.append(
            "computed content size unavailable: KLayout helper omitted the measurement"
        )

    if inspection is not None:
        print_inspection_diagnostics(
            record.team, record.top_layout, inspection, quiet_text
        )

    match = match_pins_to_text(
        pins,
        inspection.text_counts if inspection is not None else None,
    )
    project_errors.extend(match.errors)

    row = {
        "Team": record.team,
        "Top_cell": record.top_layout,
        "gds_file": Path(record.gds_saved).name if record.gds_saved else "",
        "width": format_um(inspection.width_um if inspection else None),
        "height": format_um(inspection.height_um if inspection else None),
        "computed_width": format_um(inspection.computed_width_um if inspection else None),
        "computed_height": format_um(inspection.computed_height_um if inspection else None),
        "type": PROJECT_TYPE_LABELS.get(inspection.block_type, "?") if inspection else "?",
        **readiness,
        **match.fields,
        "unmatched": match.unmatched,
        "errors": "; ".join(project_errors),
    }

    totals = Counter(pin.pad_type for pin in pins)
    return row, totals, project_errors


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Audit successfully downloaded GDS projects against info.yaml pins, "
            "digital data-port rules, and power/ground grouping."
        )
    )
    parser.add_argument(
        "report",
        type=Path,
        help="download report produced by the GDS downloader",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("gds_project_audit.csv"),
        help="output CSV file (default: gds_project_audit.csv)",
    )
    parser.add_argument(
        "--base-dir",
        type=Path,
        default=None,
        help=(
            "base directory for relative info.yaml and GDS paths "
            "(default: directory containing the report)"
        ),
    )
    parser.add_argument(
        "--klayout",
        default=os.environ.get("KLAYOUT", "klayout"),
        help="KLayout executable (default: $KLAYOUT or 'klayout')",
    )
    parser.add_argument(
        "--tech-pdk",
        type=Path,
        default=Path(os.environ.get("TECH_PDK", "/foss/pdks/gf180mcuD")),
        help="configured PDK root (default: $TECH_PDK or /foss/pdks/gf180mcuD)",
    )
    parser.add_argument(
        "--layer-properties",
        type=Path,
        default=None,
        help="KLayout PDK layer-properties file (default: <tech-pdk>/libs.tech/klayout/tech/gf180mcu.lyp)",
    )
    parser.add_argument(
        "--std-cell-lef",
        type=Path,
        action="append",
        dest="std_cell_lefs",
        default=None,
        help="standard-cell LEF file (repeat to override the default GF180 LEF set)",
    )
    parser.add_argument(
        "--helper",
        type=Path,
        default=Path(__file__).with_name("klayout_gds_inspect.py"),
        help="KLayout helper script",
    )
    parser.add_argument(
        "--quiet-text",
        action="store_true",
        help="do not print each project's unique top-level text/counts",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="return nonzero if any successfully downloaded project has an audit error",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    report_path = args.report.expanduser().resolve()
    if not report_path.is_file():
        print(f"ERROR: report not found: {report_path}", file=sys.stderr)
        return 2

    base_dir = (
        args.base_dir.expanduser().resolve()
        if args.base_dir is not None
        else report_path.parent
    )

    output_path = args.output.expanduser()
    if not output_path.is_absolute():
        output_path = Path.cwd() / output_path
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    helper_script = args.helper.expanduser().resolve()
    layer_properties = (
        args.layer_properties
        or args.tech_pdk / "libs.tech/klayout/tech/gf180mcu.lyp"
    ).expanduser().resolve()
    std_cell_lefs = (
        args.std_cell_lefs
        if args.std_cell_lefs is not None
        else sorted(
            (args.tech_pdk / "libs.ref/gf180mcu_fd_sc_mcu7t5v0/lef").glob("*.lef")
        )
    )
    std_cell_lefs = [path.expanduser().resolve() for path in std_cell_lefs]

    records = parse_download_report(report_path)
    successful = [record for record in records if record.successful]

    if not successful:
        print("ERROR: no successful GDS records found in report", file=sys.stderr)
        return 2

    overall_totals: Counter[str] = Counter()
    project_errors: list[tuple[str, str]] = []

    with output_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=CSV_HEADER)
        writer.writeheader()

        for record in successful:
            row, totals, errors = row_for_project(
                record=record,
                base_dir=base_dir,
                klayout_exe=args.klayout,
                helper_script=helper_script,
                quiet_text=args.quiet_text,
                layer_properties=layer_properties,
                std_cell_lefs=std_cell_lefs,
            )
            writer.writerow(row)
            overall_totals.update(totals)

            for error in errors:
                project_errors.append((record.team, error))
                print(f"ERROR {record.team}: {error}", file=sys.stderr)

    print()
    print(f"CSV written: {output_path}")
    print(f"Successful download records processed: {len(successful)}")

    print()
    print("Configured pad totals:")
    for pad_type in PAD_TYPES:
        print(f"  {pad_type:20s} {overall_totals[pad_type]}")

    if project_errors:
        print()
        teams_with_errors = len({team for team, _ in project_errors})
        print(f"Projects with audit errors: {teams_with_errors}")
        for team, error in project_errors:
            print(f"  {team}: {error}")

    if args.strict and project_errors:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
