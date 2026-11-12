# Chipathon 2026 Padframe Integration System

Python implementation recreated from the live `ChatGPT_spec.md` in
`d-m-bailey/padring_chipathon2026` on branch `chipathon2026`.

The separate full-chip subsystem defined by `integration_spec.md` combines
canonical project DEF/GDS artifacts and one newly generated chip-wide padring.
Its entry point is `chipathon-fullchip`, with a Makefile wrapper:

Start from `examples/integration.yaml` and `examples/base_chip.yaml`. Relative
`base_chip` paths are resolved from the integration YAML's directory.

```bash
make -f chipathon2026-system/Makefile.fullchip \
  CHIP_CONFIG=integration.yaml \
  TECH_PDK=/path/to/gf180mcuD \
  PADRING=/path/to/padring
```

Use the `validate` target to inspect project GDS PR boundaries, variant
eligibility, transformed placement, spacing, and pad-slot ownership without
running padring or producing the integrated GDS. The PR boundary is used for
placement validation; project-DEF sizing uses the physical-content bounding
box described in the specification.

Full-chip GDS assembly uses the integrated DEF as the placement authority and
requires `UNITS DISTANCE MICRONS 200`. The final layout DBU is 0.005 microns.
Each padring/project GDS is read into a separate KLayout database and copied
into its DEF-created destination cell so KLayout converts differing source
DBUs without flattening the hierarchy.

## What is implemented

- Parse and validate participant `info.yaml`.
- Require `project.lvs_config` and read the existing cf-precheck JSON format.
- Resolve `$KEY`, `${KEY}`, and `$UPRJ_ROOT` references in `LAYOUT_FILE`.
- Map the documented `io_type` values to GF180MCU I/O cells.
- Use the definitive block-variant slot orders. The A participant sequence is
  `W12..W22, N01..N11`; every listed slot is participant-configurable.
- Parse a YosysHQ padring `.cfg` as the sole physical-geometry source.
- Require immutable physical slot instance names `N01..N22`, `E01..E22`,
  `S01..S22`, `W01..W22` for production generation.
- Preserve unrelated PAD entries, comments, side, `FLIP`, AREA/GRID/CORNER/
  FILLER/LOC/SPACE directives.
- Replace only selected block-variant user slots and make unused selected slots
  `gf180mcu_fd_io__asig_5p0` placeholders.
- Detect malformed YAML, unsupported types, duplicate user pins, malformed or
  misplaced optional `secondary_esd`, too many pins, sanitization collisions,
  missing slots, fixed-instance collisions, and missing cell mappings.
- Generate machine-readable pin-to-slot metadata.
- Build/run YosysHQ padring with every LEF required by the current cell map.
- Parse GF180 LEF pin geometry and validate the project-facing terminal set.
- Generate an operator-gated virtual-interface DEF from a padring-generated DEF
  plus LEF geometry; because the canonical project DIEAREA is not finalized,
  `--diearea` is mandatory.
- Download `info.yaml`, an optional `project.export_control` document,
  `project.lvs_config`, `LAYOUT_FILE`, and LVS source files from GitHub,
  fetching fresh metadata even when local copies already exist.
- Audit a GDS download report for PR-boundary and computed physical-content
  dimensions, configured pad interfaces, and top-level text:

  ```bash
  python3 chipathon2026-system/scripts/gds_project_audit.py download.report \
    --tech-pdk "$TECH_PDK" -o build/gds_project_audit.csv
  ```
  The audit implementation and KLayout helper are in
  `chipathon2026-system/scripts/`; the authoritative requirements are in
  section 32 of the repository-level `ChatGPT_spec.md`.

## Important strictness

The current repository's `Workshop_CASS/padring/workshop_padring.cfg` is a legacy
physical template. It uses `ana*`/`config*` instance names rather than immutable
`N01/E01/S01/W01` slot names. The production converter rejects it instead of
silently assigning physical slot identities that the specification says must be
explicit.

Run:

```bash
chipathon-integrate audit-template Workshop_CASS/padring/workshop_padring.cfg
```

Once the production template itself uses immutable slot names, the A-block flow
is:

```bash
chipathon-integrate generate-padring \
  info.yaml physical_88pad.cfg \
  --block A \
  --team-code A01 \
  -o build/project_padring.cfg \
  --map-out build/project_pad_map.yaml \
  --map-json-out build/project_pad_map.json
```

Then invoke padring:

```bash
chipathon-integrate run-padring \
  --padring /path/to/padring \
  --tech-pdk "$TECH_PDK" \
  --cfg build/project_padring.cfg \
  --def-out build/project_padring.def \
  --svg-out build/project_padring.svg \
  --mapping build/project_pad_map.yaml \
  --verilog-out build/project_padring.v
```

Padring components retain canonical slot names such as `W01`. The generated
DEF and Verilog expose user-area terminals such as `W01_A`; project virtual
DEF terminals use the participant name instead, such as `RST_A`.
The layout top cell and Verilog module are named `<team-code>_padring`, such as
`A01_padring`.

The Verilog physical-pad ports use bare canonical names such as `W01`. E11,
E12, W11, and W12 are ordinary participant-configurable positions rather than
fixed ground pads. The common ground net is named from an actual participant
DVSS pad. Each DVDD pad
inherently breaks the `VDD`/`DVDD` rails and names its resulting power segment
with its canonical pad name. `BREAK ;` controls the additional physical
`brk5` isolation rather than creating the DVDD-pad electrical discontinuity.
Unpowered regions use unique explicit nets such as `FLOAT_VDD_1`. Every
break-delimited project group contains exactly one participant power pad and
one participant ground pad. Generation adds a break before the first project
I/O and after the last. A second power or second ground causes a break before
that pad and resets both group counters. The physical template contains no
fixed breaks.

The generated padring DEF has an `INOUT` Metal5 pin at the physical pad
location for every canonical slot. DVDD and DVSS canonical pins also contain
their transformed user-area-facing Metal2 LEF geometry and are marked `USE
POWER` or `USE GROUND`.

The structural Verilog includes every DEF component for LVS, including
device-containing `fill5`, `brk5`, and corner cells. Their available supply
ports connect to the common ground and neighboring canonical DVDD segment;
`brk5` connects only its available VSS port and does not bridge DVDD.
Filler instances use location-based names such as `FILL_E00_1` and
`BRK_E10_1` in both DEF and Verilog.

The template selects `fill5` with `FILLER`, selects `brk5` with `BREAKFILLER`,
and uses a parameterless `BREAK ;` between selected I/O cells. The normal gap
width is preserved, but every filler cell in that gap is `brk5` rather than
`fill5`.

The `Makefile.padframe` GDS stage also writes a
`canonical_pin_name,project_pin_name,type,x,y` CSV. Project pin names are
prefixed by the team code, for example `A01_RST`. Its
coordinates are the transformed center of the single square on GDS layer
`37/0` in each I/O macro, in microns; they are not inferred from the LEF macro
bounding box.

To generate canonical project DEFs, provide the implemented project dimensions
in microns. The command selects every minimum-area fitting configuration and
writes one DEF and one interface-mapping YAML per selected variant:

```bash
chipathon-integrate generate-project-def \
  --mapping build/project_pad_map.yaml \
  --padring-def build/project_padring.def \
  --lef "$TECH_PDK"/libs.ref/gf180mcu_fd_io/lef/gf180mcu_fd_io__in_c.lef \
        "$TECH_PDK"/libs.ref/gf180mcu_fd_io/lef/gf180mcu_fd_io__in_s.lef \
        "$TECH_PDK"/libs.ref/gf180mcu_fd_io/lef/gf180mcu_fd_io__bi_t.lef \
        "$TECH_PDK"/libs.ref/gf180mcu_fd_io/lef/gf180mcu_fd_io__bi_24t.lef \
        "$TECH_PDK"/libs.ref/gf180mcu_fd_io/lef/gf180mcu_fd_io__asig_5p0.lef \
        "$TECH_PDK"/libs.ref/gf180mcu_fd_io/lef/gf180mcu_fd_io__dvdd.lef \
        "$TECH_PDK"/libs.ref/gf180mcu_fd_io/lef/gf180mcu_fd_io__dvss.lef \
  --project-width 500 \
  --project-height 500 \
  --output-dir build/project_defs
```

## Integrated device-level LVS

Full-chip integration automatically generates the hierarchical LVS
configurations, wrappers, manifest, and sourceable environment below
`$(OUTPUT_ROOT)/lvs`. No separate LVS-environment generation target is needed.
After downloading participant data and building the integrated chip, run
device-level LVS with:

```bash
make -f chipathon2026-system/Makefile.lvs lvs \
  CHIP_CONFIG=integration.yaml \
  PDK_ROOT=/path/to/pdks \
  PDK=gf180mcuD
```

Project data must be downloaded before integration with the separate acquisition
command. The LVS Makefile never downloads or replaces project inputs:

```bash
chipathon-download-data repo.list --data-root data
chipathon-build-project-defs integration.yaml --data-root data
make -f chipathon2026-system/Makefile.fullchip CHIP_CONFIG=integration.yaml
make -f chipathon2026-system/Makefile.lvs run-lvs CHIP_CONFIG=integration.yaml
```

`chipathon-build-project-defs` reads the integration YAML and generates the
canonical project DEFs for every listed team. It uses each downloaded project's
configured GDS, measures its physical-content bounding box (non-tap/non-fill
standard cells for digital layouts, or poly/diffusion/MIM-cap geometry for
analog layouts), and runs the existing minimum-variant selection and
project-DEF flow. Analog measurement excludes cells whose names contain
`secondary_esd`, case-insensitively. The PR boundary remains for placement
validation and does not determine the selected variant. Common paths and tools
can be overridden
with `--output-dir`, `--base-chip`, `--template`, `--padring`, `--pdk-root`,
`--pdk`, `--tech-pdk`, and `--klayout`.

`DATA_ROOT`, `INTEGRATED_ROOT`, `LVS_OUTPUT_ROOT`, and `LVS_ROOT`
are overrideable. `DATA_ROOT` defaults to `data`; each participant's info,
original LVS configuration, GDS, and netlists retain their repository-relative
paths below `data/<TEAM>`. If `LVS_ROOT` is absent,
the tool clones `d-m-bailey/extra_be_checks` from the `wafer.space-run1`
branch. Integration writes generated configuration, wrappers, manifests, and
the environment below `LVS_OUTPUT_ROOT`; the LVS runner consumes those existing
artifacts and writes logs and reports there.

Use `--variant CODE` to request one particular fitting variant. By default,
GF180 Metal1 through Metal5 are blocked wherever a variant defines a blockage;
repeat `--routing-layer` to override that layer list. Coordinates are accepted
in microns and must be exactly representable in the padring DEF database grid.
Pin geometry is read from named padring DEF pins. Each project pin starts at
the I/O terminal's innermost block boundary, extends 1 micron into the block,
and is translated to a local DIEAREA beginning at `(0,0)`. Rectangle portions
outside that DIEAREA are clipped; wholly outside rectangles are omitted as long
as another positive-area rectangle remains for the terminal.
EV/CV require the first participant I/O to be power or ground, while EH/CH
require the last participant I/O to be power or ground. Incompatible variants
are excluded from automatic selection and rejected when explicitly requested.
BV, BH, D, ACV, ACH, and ACE also emit their specified Metal2-only corner
routing blockages; D and ACE have both lower-left and upper-right blockages.
Input-pad `Y` terminals use the bare user pad name. Bidirectional `Y` and `A`
terminals use `<pad>_IN` and `<pad>_OUT`, respectively; other terminal names
retain their cell-terminal suffix.
Control-terminal names default to the I/O-cell terminal case, such as
`DATA_OE`. If direct text in the selected project GDS top cell matches the
complete signal name case-insensitively, the project DEF uses that exact text
spelling instead (for example, `DATA_oe`). Conflicting spellings such as both
`DATA_OE` and `DATA_oe` are rejected. Interface metadata records both names.
For bus pins, the qualifier is inserted before the unchanged bracket suffix:
`DATA[0]` produces `DATA_PU[0]`, `DATA_PD[0]`, `DATA_IN[0]`, and
`DATA_OUT[0]` as applicable. Malformed bracket syntax is rejected.

The Makefile derives those dimensions automatically from the configured
physical-content layers and standard-cell LEFs and combines them with the
info.yaml pin count:

```bash
make -f chipathon2026-system/Makefile.padframe project-def-A01
```

By default it reads `data/A01/info.yaml`, opens its repository-relative
`project.lvs_config`, expands `LAYOUT_FILE`, and uses that exact path below
`data/A01`; directory ordering and legacy GDS directories are never used. The
PR-boundary layer remains overrideable with `PROJECT_OUTLINE_LAYER` and
`PROJECT_OUTLINE_DATATYPE`; the standard-cell LEFs and KLayout layer properties
can be overridden with `STANDARD_CELL_LEFS` and `LAYER_PROPERTIES`. One variant-specific
padring and project DEF are written for every minimum-area fitting block type.

## Download flow

```bash
chipathon-download-data repo.list --data-root data
```

This also writes `data/download_manifest.json` and a legacy-compatible
`data/download.report` for existing GDS audit scripts. To keep the report at
the old location, use:

```bash
chipathon-download-data repo.list --data-root data --report download.report
python3 scripts/gds_project_audit.py download.report -o gds_project_audit.csv
```

The report uses project-relative artifact paths and includes successful and failed
projects. Failed projects have no `GDS saved` entry and are skipped by the
audit. Progress logs remain on the console; `tee` is no longer needed to
create the report. Reports are written after project processing completes,
including runs with individual project failures.
Records follow `repo.list` order, including failed designs. Every record has
an `info.yaml` line with the project-relative saved path, including retained files in
`<TEAM>_failed` after a later error, or `none` if no file was saved.
Saved file fields (`info.yaml`, `lvs_config`, `export_control`, and `GDS saved`)
use `project-relative-path: sha256`, with the 64-digit hexadecimal SHA-256 of the saved
file. Status values such as `none` and `NOT FOUND: filename` have no checksum.
The audit parser accepts both checksummed and older plain-path reports.
File paths are relative to `data_root/TEAM` (`TEAM_failed` for failed designs).
Shared export documents use `../export_control/filename`. The `Data root`
header locates the data root relative to the report directory for auditing.
Each record includes `lvs_config` and `export_control` lines. Downloaded files
use project-relative paths. Export control is `none` when absent from `info.yaml`, or
`NOT FOUND: filename` when declared but unavailable (the project still fails).
Failed records preserve these details and point to retained files where available.
`unknown` means the project failed before its metadata could be read; invalid
export-control entries are marked `invalid: value`.

When `project.export_control` is present in a project's `info.yaml`, its
repository-relative document is saved as
`data/export_control/<TEAM>_<original-filename>`.
Both `project.export_control` and `project.lvs_config` may start with
`$UPRJ_ROOT/`; the downloader treats that prefix as the project repository root.
Within `lvs_config.json`, bare relative paths and a leading `./` on
`LAYOUT_FILE` or a participant LVS source path are treated as relative to
`$UPRJ_ROOT/`.
SPICE `.include` and `.inc` files are downloaded recursively from the same
revision, relative to the containing file (or `$UPRJ_ROOT/` when specified).
Paths may use `..` within the repository; paths escaping it are rejected.
Repeated includes and cycles are downloaded once. Absolute and external
variable-based include paths are left for the LVS environment to supply.
`LVS_SPICE_FILES_TO_FIX` is deprecated; a nonempty value is a project error.

This command recreates `data` and downloads every project in `repo.list`: its
info, original LVS configuration, GDS, and listed netlists from one immutable
repository revision. Files are written to their final repository-relative paths
as they arrive, so progress is visible on disk and an interrupted run may leave
a partial directory that must be replaced on the next run.

If an individual project download fails after its directory has been created,
the partial directory is retained as `data/<TEAM>_failed` for debugging. Other
projects continue downloading, and integration ignores the suffixed directory.
Any export-control document downloaded before the failure is also retained in
`data/export_control`.

## Install and test

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
pytest
```

See `SPEC_GAPS.md` before treating any B-E block, package mapping, transform, or
power-polarity behavior as finalized.

## Block geometry

GDS audits report missing PR_bndry as a console error and leave PR-boundary
dimensions blank, while still measuring physical content and auditing pins.
This diagnostic alone does not fail the audit, including `--strict`.
`measure_project_gds.py` also treats PR_bndry as optional metadata. Missing or
malformed boundaries print a diagnostic and produce null `prboundary_dbu` and
`prboundary_microns`; physical-content dimensions are still calculated normally.

When minimum-area selection includes corner block D or a B-family block, the
build also emits the next larger fitting non-corner alternatives: E-family
blocks for D and C-family blocks for B. The next size is determined by usable
`Area`; all tied orientations are included. Alternatives must satisfy the same
size, pin-capacity, and endpoint checks. If none fit, only the corner is emitted.
When BVE or BHE is selected, also emit A if it meets the same fit and pin
requirements. This is additional to the C-family alternatives.

Digital project selection first checks width and height. When either dimension
does not fit, the candidate can still qualify if measured bounding-box area
(`width_microns * height_microns`) is strictly below 90% of its usable `Area`.
Pin capacity and endpoint requirements always apply. Other block types retain
dimension checks only. The measurement JSON's `block_type` determines the policy;
absent classification retains dimension checks.

Project DEF generation and variant selection load the repository-root
`blocks.data` through the shared `block_data.py` loader. Padframe configuration
uses the same validated slot allocations, including all 12 slots of block D.
The TSV is accessed through the packaged `blocks.data` link. Edit this table to
change sizes, origins, pin ranges, usable areas, or blockage rectangles; restart
commands after changes. Installed packages include the table as package data.
`Pin order` defines slots (inclusive ranges); the loader requires `Pin count`
to match the number of expanded slots and rejects duplicate slots. Dimensions and
placement are in microns. The `block` column uses `x1,y1-x2,y2 & ...` and creates
placement and routing blockages on every routing layer. Blank means none.
The example base-chip YAML mirrors this table for full-chip placement.
