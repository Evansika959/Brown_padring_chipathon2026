# Chipathon 2026 Full-Chip Integration Subsystem Specification

## 1. Scope

This subsystem combines previously generated project artifacts into one complete
chip containing:

- one chip-wide padring;
- all selected user projects at legal transformed placements;
- an integrated top-level DEF;
- an integrated hierarchical GDS;
- structural top-level Verilog;
- a placement and transformation manifest;
- an overlap and validation report; and
- a CSV containing physical pad assignments and locations.

The existing padring/project-DEF subsystem remains the source of canonical
project pin orders, terminal mappings, and blockages. Chip dimensions, the
process I/O-cell mapping, and canonical block-variant origins and dimensions
come from the base-chip definition YAML referenced by the integration YAML.
The full-chip subsystem must not independently recreate or hard-code those
base-chip values.

This document defines the subsystem before implementation. No behavior may be
inferred from file ordering, dictionary ordering, clockwise terminology, or
unversioned external state when an explicit ordered definition is available.

## 2. Base-chip definition and coordinate system

Every integration YAML references exactly one base-chip definition YAML. The
base-chip file is the authoritative, reusable physical definition of a chip
profile. The initial profile is `chipathon2026_2935_v1`.

- Chip DIEAREA: `(0,0)-(2935,2935)` microns.
- Reflection center: `(1467.5,1467.5)` microns.
- Canonical project definitions describe the nominal NW placement.
- All calculations must use exact database-unit arithmetic after converting
  micron values with the inherited DEF `UNITS DISTANCE MICRONS` value.
- Inexact conversion is an error.

The reflection center is derived from the base-chip DIEAREA; it is not stored
as another independent value. Authoritative base-chip values are loaded from
YAML and are not duplicated in the integration YAML or centralized as Python
constants.

The base-chip definition has this form:

```yaml
schema_version: 1

chip:
  name: chipathon2026_2935_v1
  diearea_um: [0, 0, 2935, 2935]

io_cells:
  input_cmos: gf180mcu_fd_io__in_c
  input_schmitt: gf180mcu_fd_io__in_s
  bidirectional: gf180mcu_fd_io__bi_t
  bidirectional_24ma: gf180mcu_fd_io__bi_24t
  analog: gf180mcu_fd_io__asig_5p0
  power: gf180mcu_fd_io__dvdd
  ground: gf180mcu_fd_io__dvss

block_variants:
  A:
    origin_um: [350, 1475]
    size_um: [1110, 1110]
  BV:
    origin_um: [350, 1475]
    size_um: [550, 1110]
  BH:
    origin_um: [350, 2035]
    size_um: [1110, 550]
  CH:
    origin_um: [350, 1475]
    size_um: [1110, 550]
  CV:
    origin_um: [910, 1475]
    size_um: [550, 1110]
  D:
    origin_um: [350, 2035]
    size_um: [550, 550]
  EV:
    origin_um: [910, 2035]
    size_um: [550, 550]
  EH:
    origin_um: [350, 1475]
    size_um: [550, 550]
  ACV:
    origin_um: [350, 1475]
    size_um: [1675, 1110]
  ACH:
    origin_um: [350, 910]
    size_um: [1110, 1675]
  ACE:
    origin_um: [350, 910]
    size_um: [1675, 1675]
  ACE2:
    origin_um: [350, 350]
    size_um: [2235, 2235]
```

`io_cells` is the complete allowlist mapping participant `io_type` values to
physical I/O macro names for this base chip. An `info.yaml` pin whose `io_type`
is absent from this mapping is invalid. Empty macro names, duplicate keys, and
unknown fields are errors. Multiple I/O types may intentionally map to the
same macro.

Every selectable block variant must occur in `block_variants`. `origin_um` is
the lower-left coordinate of its canonical NW placement in top-level chip
coordinates. `size_um` is its local `(width, height)`. Values must be finite,
nonnegative, exactly convertible to the inherited DEF DBU, and describe a
positive-area rectangle wholly inside the chip DIEAREA. The variant's slot
order, pin limit, special endpoint rules, placement/routing blockages, and
other project-DEF policy remain defined by the canonical project-DEF
specification. The base-chip variant set and canonical variant set must match;
missing or extra variants are errors.

## 3. Integration YAML

The input YAML has this form:

```yaml
schema_version: 2

base_chip: base_chips/chipathon2026_2935_v1.yaml

chip:
  name: chipathon_example
  minimum_project_gap_um: 10

projects:
  - team: A01
    variant: A
    quadrant: NW
  - team: D05
    variant: EV
    quadrant: SE
```

Required top-level keys are `schema_version`, `base_chip`, `chip`, and
`projects`. `base_chip` is a path to the base-chip definition YAML. A relative
path is resolved relative to the directory containing the integration YAML,
not relative to the current working directory. The resolved file path and its
content hash are recorded in the placement manifest.

The integration YAML does not repeat the floorplan name, DIEAREA, I/O-cell
mapping, or block origins and sizes. Those values are obtained solely from the
referenced base-chip definition. Conflicting command-line overrides for these
authoritative values are not permitted.

Each project entry contains exactly the logical selection data:

- `team`: participant team code;
- `variant`: one authoritative canonical block variant; and
- `quadrant`: one of `NW`, `NE`, `SE`, or `SW`.

Artifact roots, PDK paths, tool paths, layer overrides, and output paths are
command-line or Makefile parameters, not per-project YAML overrides.

The same team may not appear more than once unless a future schema explicitly
defines multiple instances. Multiple different projects may occupy the same
quadrant when all geometry and spacing checks pass.

## 4. Project artifact discovery

For each team, the subsystem reads:

- `data/<team>/info.yaml` from an overrideable common data root;
- the original project `lvs_config.json` at the repository-relative path named
  by `project.lvs_config` in that `info.yaml`;
- the project GDS at the repository-relative path obtained by expanding
  `LAYOUT_FILE` in that `lvs_config.json`; and
- the previously generated canonical project DEF for the requested variant
  from an overrideable project-artifact root.

The first GDS file in a directory must never be selected implicitly. Integration
and every audit or validation program must resolve project GDS data through the
project's downloaded `info.yaml` and `lvs_config.json` below the common data
root. The legacy `info/<team>_*` and `gds/<team>` locations are not fallback
inputs.

The existing canonical project DEF is reused. Its variant, team mapping,
dimensions, DBU, pins, blockages, and interface metadata must agree with the
requested project.

## 5. Project eligibility validation

Before placement, the subsystem validates each project against the selected
variant:

1. Load and validate `info.yaml` using the existing participant schema.
2. Reject the project when its participant pin count exceeds the variant's
   maximum pin count.
3. Apply all variant endpoint constraints from the canonical subsystem, such as
   required power/ground placement at the first or last allocated I/O.
4. Resolve the authoritative project GDS through `LAYOUT_FILE`.
5. Require exactly one project GDS top cell.
6. Require exactly one rectangle and no other shapes directly in that top cell
   on the configured PR-boundary layer/datatype, default `0/0`.
7. Require the PR-boundary rectangle lower-left coordinate to be exactly
   `(0,0)`.
8. Require positive PR-boundary width and height.
9. Require the PR-boundary width and height to fit the selected variant's
   usable width and height.
10. Require the canonical project DEF DIEAREA, origin assumptions, variant,
    and measured GDS PR boundary to agree.

Both PR-boundary size and origin are mandatory checks. A size-only bounding-box
test is insufficient.

## 6. Quadrant transformations

For a point `(x,y)` in the nominal NW coordinate system and the base-chip
DIEAREA `(X0,Y0)-(X1,Y1)`, use `W=X1-X0`, `H=Y1-Y0`:

```text
NW: (x,     y)
NE: (X0 + X1 - x, y)
SE: (X0 + X1 - x, Y0 + Y1 - y)
SW: (x,     Y0 + Y1 - y)
```

For rectangles and polygons, transform every vertex and normalize the result.
Do not transform only a lower-left coordinate.

The equivalent DEF orientations are:

| Quadrant | Orientation | Operation |
| --- | --- | --- |
| NW | N | identity |
| NE | FN | reflection across the chip Y axis |
| SE | S | reflection across both chip axes / 180-degree rotation |
| SW | FS | reflection across the chip X axis |

The transform applies consistently to:

- project PR boundaries;
- project GDS instances;
- project DEF components and pins;
- canonical project blockages;
- physical I/O-slot allocation;
- pad-facing and user-facing pin geometry; and
- pin-location reporting.

## 7. Physical I/O-slot derivation

The canonical slot order for a variant is the NW definition. Other quadrant
orders are derived geometrically from immutable physical slot coordinates.
They must not be maintained as independent hand-written tables.

For the A variant, the resulting authoritative examples are:

```text
NW: W12-W22, N01-N11
NE: E12-E22, N22-N12
SE: E11-E01, S22-S12
SW: W11-W01, S01-S11
```

The transformed ordered list must contain the same number of slots as the
canonical list, contain no duplicates, and map each canonical slot to exactly
one physical slot.

Two projects may not allocate the same physical I/O slot.

### 7.1 Per-pad orientation after quadrant transformation

Quadrant transformation also controls the orientation of each project-owned
I/O cell. The padring configuration supports this per cell with the optional
`FLIP` modifier on an individual `PAD` directive. The integration subsystem
must emit that modifier where required. No configuration-grammar or row-
placement algorithm change is required. Output writers must use the placement
engine's resolved position directly without adding their own flip-dependent
translation.

Apply `FLIP` to every project-allocated I/O cell, including signal, analog,
power, and ground cells, matching the following transformed quadrant and side:

| Project quadrant | Transformed padring side | Required reflection | `FLIP` |
| --- | --- | --- | --- |
| NW | any | none beyond normal side placement | no |
| NE | N | about the Y axis | yes |
| NE | E | about the Y axis | yes |
| SE | S | none beyond normal side placement | no |
| SE | E | none beyond normal side placement | no |
| SW | W | about the X axis | yes |
| SW | S | about the X axis | yes |

Any quadrant/side combination not listed as requiring reflection retains the
normal orientation for that physical side. This rule applies to allocated I/O
cells, not automatically to corners, fillers, break fillers, or unused slot
placeholders.

Equivalently, using the NW project as the nominal orientation:

- NW and SE projects use normal padring cell orientations;
- NE projects flip their north- and east-side I/O cells; and
- SW projects flip their south- and west-side I/O cells.

`FLIP` changes orientation about the placed cell's bounding-box center. It
must not change the occupied physical slot or translate the cell into an
adjacent gap. After placement, the normalized top-level bounding box of each
flipped I/O-cell instance must be identical to the bounding box produced for
the same cell in that slot without `FLIP`; only the internal geometry and pin
orientation may differ. A bounding-box coordinate or dimension change is a
generation error.

Padring's placement engine supplies the physical slot coordinate. DEF, GDS,
and SVG writers must not add a second flip-dependent X or Y translation.
Changing `FLIP` therefore changes only orientation; its additional placement
offset is exactly `(0,0)`. In particular, writers must not add or subtract the
75-micron GF180 I/O-cell width.

If a future physical template supplies an explicit orientation for an
allocated slot, the generator must resolve the requested project reflection
against that orientation deterministically and emit the effective per-pad
orientation exactly once. It must not produce duplicate orientation tokens or
silently discard the project reflection.

## 8. Chip-wide padring generation

The subsystem generates one new chip-wide padring configuration from all
project mappings and runs padring exactly once.

It must not overlay multiple project-specific 88-pad padring DEF files.

Generation proceeds in deterministic project/YAML order while preserving each
project's authoritative internal pin order. For every project:

- place its transformed I/O cells in its derived physical slots;
- apply the per-pad `FLIP` rules in section 7.1;
- retain a break immediately before its first allocated I/O cell;
- retain a break immediately after its last allocated I/O cell, including when
  the next project begins in an adjacent slot range;
- preserve the canonical power/ground grouping rules inside the project; and
- fill all unallocated physical slots with the canonical unused placeholder
  policy.

Break gaps contain only the configured break-cell family. A project boundary
must never be removed as an optimization.

The generated padring top cell and Verilog module are named
`<chip_name>_padring`.

After padring runs, validate every allocated I/O-cell instance against its
expected physical slot bounding box. Report the quadrant, side, effective
orientation, and pre-/post-reflection bounding boxes in the placement manifest.

## 9. Project spacing and overlap validation

Transform every project PR boundary into chip coordinates before comparison.
Reject the integration if:

- a transformed PR boundary extends outside the chip DIEAREA;
- two transformed project PR boundaries overlap;
- the edge-to-edge separation between two project PR boundaries is less than
  `minimum_project_gap_um`, default 10 microns;
- transformed project placement or routing blockages conflict with another
  project's allowed area;
- two projects allocate the same physical I/O slot; or
- any transformed geometry is inconsistent between DEF, GDS, and the placement
  manifest.

Exactly 10 microns of separation is legal. Boundary touching is not legal
because it provides zero separation.

The validation report lists every project pair checked, their transformed
boundaries, measured separation, and pass/fail result. It must report all
detected conflicts in one run where practical rather than stopping after the
first pair.

## 10. Project GDS isolation and cell naming

Each project GDS is read into a new, separate `pya.Layout`. It must never be
read directly into the destination chip layout. The source layout retains the
DBU declared by that GDS, so projects with different source DBUs remain
independent during import.

For each source, require exactly one top cell. Create or resolve the
team-prefixed destination project cell corresponding to the component already
created by the integrated DEF, then copy the source hierarchy with
`destination_project.copy_tree(source_top)`. KLayout performs source-to-
destination DBU conversion during this cross-layout copy. Do not manually
rescale integer source coordinates and do not place a second project instance
in addition to the instance created from DEF.

Project hierarchies must remain logically separate while composing the
integrated layout. Before or during `copy_tree`, apply an explicit destination
cell-name mapping so a source subcell cannot be merged with an unrelated
same-named cell already in the destination. Participant hierarchy cells are
team-namespaced when necessary, and the final mapping is recorded.

Team-code matching is case-insensitive. Each project top cell in the integrated
copy must begin with its team code. If it does not, rename the integrated copy
to `<team_code>_<original_top_cell>`. Never modify the participant's source GDS.

The top-cell rename map records:

- team code;
- source GDS;
- original top-cell name;
- integrated top-cell name; and
- final exported top-cell name.

For tapeout GDS export, disable KLayout cell-context saving. No project library
proxy or context cell is permitted in the destination or exported hierarchy.
After writing, reopen the exported GDS and validate that:

- every cell has a globally unique stream name;
- each project instance resolves to the intended hierarchy;
- no source hierarchy was silently merged with another project;
- the integrated top cell is unique; and
- no KLayout context helper top cell remains.

The placement manifest records KLayout's final cell-name mapping, including any
automatic uniqueness suffixes.

## 11. Integrated GDS and DBU conversion

The integrated GDS top cell is exactly `<chip_name>`.

The final destination KLayout database has `layout.dbu = 0.005` microns, which
is 200 database units per micron. The integrated DEF must therefore contain
`UNITS DISTANCE MICRONS 200`. A mismatch is a fatal error; the final GDS DBU is
not overrideable in this initial profile.

Create the destination chip layout and its `<chip_name>` top cell by reading
the integrated top-level DEF with the required LEFs into this canonical
0.005-micron layout. The DEF is authoritative for top-level component
placements and orientations. Populate the DEF-created padring and project
component cells by copying their GDS hierarchies into those destination cells.

The required project import sequence is:

1. Create a fresh source `pya.Layout` for one project.
2. Read only that project's GDS into the source layout and retain its original
   `source.dbu`.
3. Require exactly one source top cell.
4. Resolve the project cell created in the destination by the integrated DEF.
5. Call `destination_project.copy_tree(source_top)` using an explicit,
   collision-safe cell mapping where required.
6. Verify that physical dimensions in microns are unchanged by the copy and
   that the DEF-created instance remains the sole top-level project instance.

Repeat with a new source layout for every project. Never reuse a source layout
and never infer that all source GDS files use the same DBU.

It contains:

- one `<chip_name>_padring` instance; and
- one transformed instance of each team-prefixed project top cell.

The project GDS geometry is not flattened. Hierarchy is preserved through
tapeout export subject to the globally unique cell-name validation above.

At every physical pad location, add the actual top-level project pin name as a
GDS text object. The name is prefixed with the team code unless it already
starts with that team code case-insensitively. Prefixing uses
`<team_code>_<project_pin_name>`.

The text location is the physical-pad center used by the chip pin CSV. The text
layer/texttype is the I/O-cell Metal5 label layer, default `81/10`, and is
overrideable by command-line or Makefile parameters. The integrated GDS and CSV
must use the same resolved name and coordinates.

DEF-reader-generated top-level pin text on this layer must be suppressed or
removed before inserting the CSV-derived labels. The final top cell contains
exactly one text object per CSV row, at the CSV coordinate; it must not retain
a second DEF-derived copy at the DEF pin-geometry coordinate.

Reject case-insensitive collisions between final top-level project pin names.

## 12. Integrated top-level DEF

The integrated DEF design name is exactly `<chip_name>` and inherits the
chip-wide padring DEF units.

Its DIEAREA is the referenced base-chip DIEAREA. It contains:

- the chip-wide padring as a component;
- one component for each transformed project top cell;
- transformed project pins and blockages required for top-level connectivity;
- physical top-level pad pins corresponding to the chip-wide padring; and
- nets connecting each padring user-facing terminal to its project terminal.

Project component names are deterministic and team-prefixed. DEF and GDS
placement transforms must agree exactly after unit conversion.

The integrated DEF must not silently discard a canonical project blockage or
move a project to satisfy spacing.

Both copied/transformed and newly emitted blockage geometry must use DEF
`RECT` syntax without a `+` prefix, for example:

```def
- PLACEMENT RECT ( x1 y1 ) ( x2 y2 ) ;
- LAYER Metal2 RECT ( x1 y1 ) ( x2 y2 ) ;
```

The full-chip blockage reader must parse this standards-compliant form. It
must not require or generate the invalid legacy form `+ RECT`.

## 13. Structural Verilog

Generate:

1. the structural `<chip_name>_padring` Verilog module; and
2. the structural `<chip_name>` top-level module.

The top-level module instantiates:

- one `<chip_name>_padring` instance; and
- one externally defined project module instance for every project.

The generated output does not emit black-box module declarations or copy user
project module definitions. User project Verilog is supplied externally.

Each project module name uses the case-insensitively team-prefixed integrated
top-cell/module name policy. Each instance name is deterministic and
team-prefixed.

Connections use the resolved participant terminal names from project interface
metadata. Bidirectional pad terminal mappings remain:

```text
I/O-cell Y -> <project-pad>_IN
I/O-cell A -> <project-pad>_OUT
```

Control signals, bus suffixes, power, ground, and analog terminals retain the
existing canonical project-interface rules. Top-level signal names are
team-prefixed unless already prefixed case-insensitively.

An input cell's external physical PAD net and project-facing `Y` net must not
be shorted merely because both derive from the same participant pad name. The
external port and GDS label retain `<team>_<pad>`, while the structural wrapper
uses a distinct internal net such as `<team>_<pad>__CORE` to connect padring
terminal `Y` to the project's bare `<pad>` port. Direct power, ground, and
analog pad connections remain intentionally common.

The structural Verilog hierarchy and integrated GDS hierarchy must contain the
same chip, padring, and project instances.

## 14. Pin CSV

Write a CSV with this header:

```text
team_code,project_pin_name,canonical_pad_name,type,x,y
```

For each allocated physical I/O pad:

- `team_code` is the normalized participant team code;
- `project_pin_name` is the final team-prefixed top-level name;
- `canonical_pad_name` is the final physical slot such as `N03` or `E18`;
- `type` is the participant `io_type`;
- `x` and `y` are the physical-pad center in microns.

Pad centers use the existing process-configured marker geometry algorithm, not
LEF macro centers. Names and coordinates must match the GDS text objects.

## 15. Placement and transformation manifest

The machine-readable manifest records at least:

- specification/schema version;
- chip name, base-chip name, resolved base-chip path and hash, DIEAREA, DBU,
  I/O-cell mapping, and minimum gap;
- the resolved origin and size of every selected block variant;
- all resolved input and output paths;
- project team, variant, quadrant, pin count, and measured PR boundary;
- each source GDS DBU, the destination `0.005`-micron DBU, and the verified
  source/destination physical bounding boxes;
- canonical and transformed PR boundaries;
- exact DEF and GDS transforms;
- canonical-to-physical I/O-slot mapping;
- requested and effective per-pad orientation plus pre-/post-flip bounding
  boxes;
- source and final project top-cell names;
- final hierarchy cell-name mapping;
- resolved project-pin names;
- padring break boundaries;
- all validation results; and
- hashes of input YAML, info files, mappings, DEFs, GDS files, and generated
  outputs where practical.

The manifest must be sufficient to reproduce and audit every placement and
name transformation.

## 16. Validation report

Produce a human-readable and machine-readable validation report. At minimum it
reports:

- integration-YAML and base-chip-YAML schema validation;
- base-chip/canonical variant-set agreement and I/O-type allowlist validation;
- artifact discovery and `LAYOUT_FILE` resolution;
- pin-count and variant eligibility;
- PR-boundary origin and size validation;
- project/chip containment;
- pairwise overlap and 10-micron spacing;
- duplicate physical-slot allocation;
- per-pad quadrant/side orientation and bounding-box invariance;
- project pin-name collision checks;
- DEF/GDS/Verilog instance consistency;
- imported hierarchy and exported cell-name uniqueness;
- source-GDS DBU preservation, cross-layout DBU conversion, and final
  `0.005`-micron DBU validation;
- padring generation and project-boundary breaks;
- GDS text/CSV name and coordinate consistency; and
- output existence and top-cell/module naming.

Any failed mandatory check makes the integration command fail with a nonzero
exit status. Partial outputs must be clearly marked invalid and must not be
presented as tapeout-ready.

## 17. Command-line and Makefile interface

The subsystem has its own CLI entry point or a clearly separated command group.
It accepts:

- integration YAML;
- common project-data root, defaulting to `data`;
- project-artifact root;
- PDK/tool paths;
- output root;
- PR-boundary layer/datatype overrides;
- Metal5 text layer/texttype overrides, default `81/10`; and
- an optional validation-only mode.

Per-project paths are not embedded in the integration YAML. Global roots and
tool settings are overrideable on the command line and in the Makefile.
The base-chip path is normally obtained from the integration YAML; a CLI or
Makefile base-chip-path override may replace only the path used to locate the
file, not individual values within it, and the resolved override must be
recorded in the manifest.

The normal build performs validation before expensive padring or GDS work and
validates the final exported artifacts again afterward.

## 18. Initial implementation boundaries

The first implementation includes the outputs and checks defined above. It does
not include:

- automatic relocation to repair overlap or spacing failures;
- arbitrary rotations beyond the four defined quadrant transforms;
- per-project path overrides inside integration YAML;
- flattening participant GDS hierarchies;
- synthesized black-box declarations for user modules; or
- support for a project appearing multiple times in one chip.

These require explicit future specification changes.

## 19. Integrated device-level LVS

### 19.1 Scope and authoritative LVS implementation

The integration subsystem shall generate the local source tree and hierarchical
`lvs_config.json` files needed to run device-level LVS between the integrated
GDS and the generated structural Verilog hierarchy.  It shall also provide a
command that invokes the LVS run and reports its result.

The authoritative LVS implementation is
[`d-m-bailey/extra_be_checks`](https://github.com/d-m-bailey/extra_be_checks),
checked out from the `wafer.space-run1` branch.  Its checkout directory is
`LVS_ROOT`.
If no checkout is available at the configured location, the command shall clone
that repository and branch.  An existing directory must not be deleted or
silently replaced.  An existing checkout must be validated for the expected
repository and branch; a mismatch is an error with corrective instructions.

The integrated top-level configuration shall include
`$LVS_ROOT/tech/$PDK/lvs_config.io.json`.  That file is responsible for the
standard-cell and GF180 I/O device-level netlists, so the generated top-level
configuration must not add those PDK netlists separately.  A missing file is a
fatal configuration error.

### 19.2 Inputs

The LVS flow consumes:

- the integration YAML and its project list;
- the existing `repo.list` team-to-`owner/repository` mapping;
- `data/<team>/info.yaml` and the repository-relative project LVS
  configuration referenced by it;
- the integrated GDS;
- the generated chip-level and padring structural Verilog;
- each project's downloaded SPICE and/or Verilog netlists; and
- the configured `LVS_ROOT`, common data root, PDK, work, log, signoff, and
  output roots.

Team lookup remains case-insensitive and uses the canonical team code selected
by integration.  Every integrated project must have exactly one repository
mapping.  Missing or ambiguous mappings are errors.

### 19.3 Unified project download stage

GDS and netlist acquisition must be one repository-list download operation, not
independent GDS and LVS download workflows. The old external
`download_chipathon_gds_v2_lfs.py` program is replaced by a downloader inside
the `chipathon2026-system` package and is no longer part of the documented or
invoked workflow. The downloader does not consume an integration YAML. For
every team/repository entry in `repo.list`, the same invocation shall download:

- `info.yaml`;
- the `lvs_config.json` named by `project.lvs_config`;
- the GDS named by the resolved `LAYOUT_FILE`;
- every nonempty path in `LVS_SPICE_FILES`; and
- every nonempty path in `LVS_VERILOG_FILES`.

Both a JSON string and a JSON list are accepted for either source-file field.
Empty strings are ignored.  Variables defined by the project configuration are
resolved before repository paths are selected.  `$UPRJ_ROOT` denotes the root
of that project's source repository during download.  Any other unresolved
variable in a downloadable path is an error.

Ordinary GitHub files and Git LFS pointers are supported for both layout and
netlist data.  Every transfer is checked for HTTP success; LFS objects are
additionally checked for declared size and SHA-256 identity.

The downloader prints progress before every file transfer. Each message
identifies the team, repository and immutable revision, repository-relative
source path, and final local destination. Output is flushed so progress remains
visible during long GDS and Git LFS transfers.

Repository probing assumes the branch is `main`. The downloader first requests
`info.yaml` from `main` without querying the GitHub repository metadata API.
Only when `main/info.yaml` is missing may it discover a different default
branch, using the Git remote symbolic `HEAD`; it must not use a per-repository
GitHub API request for the normal path. The selected branch is then resolved to
one immutable commit before downloading the project artifact set.

All downloaded project artifacts preserve their full repository-relative paths
below one common root:

```text
<data_root>/<TEAM>/info.yaml
<data_root>/<TEAM>/<project.lvs_config repository-relative path>
<data_root>/<TEAM>/<LAYOUT_FILE repository-relative path>
<data_root>/<TEAM>/<LVS source repository-relative path>
```

For example, a participant layout referenced as
`$UPRJ_ROOT/final/gds/chip_top.gds` and netlist referenced as
`$UPRJ_ROOT/final/pnl/chip_top.pnl.v` are stored as:

```text
data/A01/final/gds/chip_top.gds
data/A01/final/pnl/chip_top.pnl.v
```

Files are not flattened, renamed, or copied into separate GDS and netlist
trees. Within generated configurations, `<TEAM>_ROOT` is the absolute path to
`<data_root>/<TEAM>`, so the repository-relative suffix remains unchanged.

Relative `include` directives inside SPICE or Verilog source files are not
supported as a means of discovering additional files.  Every required source
file must be listed by the project LVS configuration.  Preserving directories
is required to avoid basename collisions and to provide deterministic source
provenance.

If `data_root` exists when the command starts, the downloader asks for
confirmation to remove and recreate the complete directory. Declining aborts
without changing it. A `--yes` option may provide confirmation for unattended
use. The downloader rejects unsafe roots such as the filesystem root, the
current working directory, symlinks, and non-directory paths. If `data_root`
does not exist, it is created automatically without prompting.

To avoid filesystem races from tools that recreate metadata such as macOS
`.DS_Store`, replacement first atomically renames the existing data directory
to a unique hidden retirement path and then recursively deletes the renamed
tree. If deletion fails, the downloader restores the old directory when
possible and aborts before downloading. It then creates the new `data_root`.

After this initialization, each downloaded file is written immediately to its
final repository-relative location. The downloader must not retain the complete
multi-project artifact set in memory or defer all filesystem output until every
repository has been downloaded. Consequently, interruption or a failed
transfer may leave a visibly partial new data tree; a later invocation must
confirm removal of that tree before retrying. A malformed, duplicate, or
unknown repository-list entry is a command-level error.

Any expected failure while downloading or validating an individual project is
isolated to that project. This includes malformed YAML or JSON, missing files,
HTTP and Git failures, invalid configuration, unsupported relative includes,
and local file-write errors. The downloader prints a concise team-qualified
error rather than a Python traceback, removes the partial
`<data_root>/<TEAM>` tree so it cannot be consumed, records the failure in the
manifest, and continues with the remaining repositories. The command exits
nonzero after completing the other downloads.

At the end of the operation, the downloader prints the number of successful
and failed projects, followed by every failed project code and its recorded
reason. The final summary also reports total elapsed wall-clock download time
in `HH:MM:SS.mmm` form, including when a command-level error aborts the run.
Timing begins only after the user accepts data-directory removal when a prompt
is required; time spent waiting for confirmation is excluded. Without a
prompt, timing begins immediately before data-root initialization.

There shall be one canonical integration command and Makefile target for this
unified download. No separate GDS-only downloader is used by integration or
audit commands. The manifest records one resolved revision per project and
applies it to all files for that project; mixing files from different revisions
is an error.

Full-chip integration and audit commands subsequently select only the teams
named by their integration YAML from this complete downloaded snapshot.
Projects that are not part of a particular chip remain downloaded and
available for other integrations.

### 19.4 Rewritten project configurations

For each project, generate a derived configuration named
`<TEAM>_lvs_config.json`.  Never modify the downloaded original.  The derived
configuration defines a team-specific root variable, for example:

```json
"A01_ROOT": "<data_root>/A01"
```

Every source path rooted at `$UPRJ_ROOT` is rewritten to the corresponding
team-root variable while preserving the repository-relative suffix.  For
example:

```text
$UPRJ_ROOT/team_src/magic/chip_top_golden.spice
```

becomes:

```text
$A01_ROOT/team_src/magic/chip_top_golden.spice
```

The generated path must resolve to a downloaded regular file. Project fragments
reference, but do not redefine, their `<TEAM>_ROOT` variable. String and list
forms are normalized to lists, empty entries are removed, ordering is
preserved, and duplicate resolved paths are rejected.

Project extraction and comparison settings required by that project, including
`EXTRACT_FLATGLOB`, `EXTRACT_ABSTRACT`, `LVS_FLATTEN`, `LVS_NOFLATTEN`, and
`LVS_IGNORE`, are preserved.  Project `LAYOUT_FILE`, `TOP_LAYOUT`, and other
project-run scalar values must not override the integrated top-level values.
The generated project files act as hierarchical source/configuration fragments;
the generated chip-level file owns the final integrated layout and top-cell
settings.

### 19.5 Integrated top-level configuration

Generate one chip-level `lvs_config.json` that hierarchically includes, in
deterministic order:

1. `$LVS_ROOT/tech/$PDK/lvs_config.io.json`; and
2. one generated project configuration for every integrated project.

It shall set `TOP_LAYOUT` and `TOP_SOURCE` to the integrated chip name,
`LAYOUT_FILE` to the integrated GDS, and add the generated padring and chip-level
structural Verilog in leaf-to-root order.  Project netlists precede the padring
and chip-level wrappers.  The emitted configuration must contain no unresolved
project-root variables and every referenced local file must exist before LVS
is started.

The top-level configuration defines one absolute `<TEAM>_ROOT` value for every
integrated project. The generated LVS environment script exports the identical
set of project-code variables, and the direct LVS runner passes those same
values in its subprocess environment. Thus each derived project configuration
only needs to replace `$UPRJ_ROOT` with `$<TEAM>_ROOT` while preserving the
repository-relative suffix.

### 19.6 Renamed project-top wrappers

If integration changed a project's layout top-cell name to enforce the
team-prefix rule, LVS shall use a generated source wrapper with the same final
name rather than editing the participant's downloaded netlist.  The wrapper
instantiates the participant's original top module or subcircuit and preserves
the exact terminal names and order required by the source format and project
interface metadata.  No wrapper is generated when source and integrated layout
top names already agree.

Generated wrapper names, wrapped source names, source file, language, and port
mapping are recorded in the LVS manifest.  Failure to find exactly one source
definition for the expected project top is an error.

### 19.7 Cross-project source-name validation

Before LVS, parse all downloaded and generated source definitions and construct
a case-sensitive name-to-project ownership table.  If the same non-standard
module, subcircuit, or model name is defined by more than one project, report
every owner and source location and fail before comparison.  This includes
conflicting subordinate-cell names, not only project tops.

Repeated standard-cell definitions are permitted silently.  A definition is a
standard cell when it belongs to a standard-cell library identified by the
project configurations or by `lvs_config.io.json`; GF180 standard-cell naming
alone may be used only when it agrees with those configured libraries.  I/O
cells and arbitrary participant cells are not implicitly covered by this
exception.

### 19.8 LVS environment setup

The integration program, rather than the user, is responsible for constructing
the LVS runtime environment. Configuration generation shall emit a sourceable
POSIX shell file named `<chip_name>_lvs.env`. The LVS runner shall construct and
pass the identical environment directly to `run_full_lvs`; sourcing the file
manually is optional and must produce the same values.

The environment file shall export at least:

- `LVS_ROOT`, resolved to the validated `extra_be_checks` checkout;
- `PDK_ROOT` and `PDK`;
- `WORK_ROOT`, `LOG_ROOT`, and `SIGNOFF_ROOT` below the configured LVS output
  root;
- `INTEGRATION_ROOT`, containing the integrated GDS and generated top-level and
  padring Verilog;
- `LVS_BUILD_ROOT`, containing generated LVS configurations and wrappers; and
- one absolute `<TEAM>_ROOT` for every downloaded project source tree.

All emitted values must be shell-quoted safely. Environment files must not
contain authentication tokens or inherit unrelated project-root variables.
Required directories are created before execution. Existing caller values for
these variables must not silently override the generated values; explicit CLI
or Makefile path options are resolved first and then written into the generated
environment.

Before invoking LVS, validate the environment by processing the generated
top-level configuration through `$LVS_ROOT/set_lvs_env.py`. Any undefined
variable, missing included configuration, missing source/layout file, or
disagreement between the environment file and direct runner environment is a
fatal preflight error.

The environment path, effective values, selected PDK, and hashes of the
generated environment and configuration files are recorded in the LVS
manifest. Because the environment contains machine-local absolute paths, it is
a generated build artifact and not an input committed as portable project
configuration.

### 19.9 Execution, outputs, and safety

Successful full-chip integration always generates the LVS configurations,
wrappers, manifest, and environment as integration outputs. There is no
separate command or Makefile target for LVS-environment or configuration
generation. The user-facing flow provides a project-data download command, the
full-chip integration command, and an LVS-run command. The run stage consumes
the artifacts already emitted by integration and the exact project files under
`data_root` that integration validated. It must not download, replace, or
otherwise mutate project input data. Project acquisition remains exclusively a
separate pre-integration operation. The run stage invokes:

```text
$LVS_ROOT/run_full_lvs <generated-chip-lvs-config>
```

with the complete generated environment described above. The command returns
nonzero for any prerequisite, download, environment preflight, configuration,
extraction, collision, or LVS comparison failure.

Outputs include:

- the generated chip and per-project LVS configurations;
- the generated sourceable LVS environment file;
- any generated project-top wrappers;
- a download/configuration manifest containing repositories, branch/revision,
  remote paths, local paths, sizes, and hashes;
- the exact effective command and environment roots;
- extraction and Netgen logs;
- the device-level LVS report; and
- a concise machine-readable and human-readable pass/fail summary.

No downloaded participant source is modified in place.  A failed run retains
diagnostic outputs but must mark them invalid and must not report the integrated
design as LVS-clean.
