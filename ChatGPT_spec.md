# **Chipathon 2026** **Padframe Integration Specification**

*Consolidated working specification for padframe, I/O, padring, DEF, and project integration*

*This document is intended to be editable and reusable as the starting context for a new development chat.*

# **1\. Objective**

Develop a Python-based integration system for the 2026 SSCS Chipathon using the GF180MCU process.

The separate full-chip integration and integrated device-level LVS subsystem is
specified normatively in `integration_spec.md`. Changes to that subsystem must
update that specification before or together with implementation changes.

The system must:

1\. Read each project's info.yaml.

2\. Locate the project's existing lvs\_config.json.

3\. Use the existing cf-precheck lvs\_config.json format without redefining it.

4\. Read the project's I/O pin definitions from info.yaml.

5\. Convert those pin definitions into GF180MCU I/O pad cells.

6\. Generate a YosysHQ padring configuration from a fixed physical padframe template.

7\. Generate the project-side DEF / virtual padframe used as an implementation constraint.

8\. Allow a completed project to be transformed into any valid physical block location on the final die without rerouting its project-side interface.

9\. Ultimately generate the final top-level padframe and project placement.

The target I/O library is:

gf180mcu\_fd\_io

The padring generator is:

https://github.com/YosysHQ/padring

# **2\. Existing lvs\_config.json**

The lvs\_config.json format is already defined by cf-precheck. Do not create a new format.

The authoritative specification is the existing ChipFoundry cf-precheck documentation.

info.yaml identifies the file as:

project:  
  lvs\_config: "lvs\_config.json"

or, if stored elsewhere:

project:  
  lvs\_config: "lvs/my\_design/lvs\_config.json"

The parser must therefore access:

config\["project"\]\["lvs\_config"\]

or defensively:

config.get("project", {}).get("lvs\_config")

The integration program then reads the referenced lvs\_config.json.

The existing configuration contains the layout information such as:

TOP\_LAYOUT  
LAYOUT\_FILE

and other cf-precheck parameters.

Variable references such as:

$TOP\_SOURCE  
${TOP\_SOURCE}  
$UPRJ\_ROOT

may need to be resolved according to the existing precheck conventions.

# **3\. info.yaml pin specification**

The participant-facing YAML must remain simple.

Example:

project:  
  title: "Example Project"  
  team: "Example Team"  
  description: \>  
    Example project.  
  lvs\_config: "lvs\_config.json"

pins:  
  \- name: reset\_n  
    io\_type: input\_cmos

  \- name: clock  
    io\_type: input\_schmitt

  \- name: gpio0  
    io\_type: bidirectional

  \- name: high\_drive\_gpio  
    io\_type: bidirectional\_24ma

  \- name: "sensor\[0\]"  
    io\_type: analog  
    secondary\_esd: true

Each pins: entry represents exactly one physical I/O cell.

The order of entries in pins: is significant.

pins:  
  \- name: first\_pin  
    io\_type: bidirectional

  \- name: second\_pin  
    io\_type: bidirectional

This means first\_pin is assigned to the first available physical project pad slot and second\_pin to the next one.

# **4\. Pin-name quoting**

Simple pin names do not require quotes:

\- name: reset\_n  
  io\_type: input\_cmos

Pin names containing punctuation should preferably be quoted:

\- name: "data\[7\]"  
  io\_type: bidirectional

This is especially important when YAML flow syntax is used:

\- { name: "data\[7\]", io\_type: bidirectional }

because this is invalid or ambiguous:

\- { name: data\[7\], io\_type: bidirectional }

Block-style YAML is preferred.

# **5\. Supported io\_type values**

The integration system maintains the mapping from user-facing io\_type to GF180 I/O cell.

Initial mapping:

| io\_type | GF180 cell |
| :---- | :---- |
| input\_cmos | gf180mcu\_fd\_io\_\_in\_c |
| input\_schmitt | gf180mcu\_fd\_io\_\_in\_s |
| bidirectional | gf180mcu\_fd\_io\_\_bi\_t |
| bidirectional\_24ma | gf180mcu\_fd\_io\_\_bi\_24t |
| analog | gf180mcu\_fd\_io\_\_asig\_5p0 |
| power | gf180mcu\_fd\_io\_\_dvdd |
| ground | gf180mcu\_fd\_io\_\_dvss |

The mapping must be maintained in the integration code rather than duplicated in every user's YAML.

# **6\. Output-only signals**

A separate output-only io\_type is not required unless a distinct physical GF180 I/O cell is later selected.

Normal digital outputs may use:

gf180mcu\_fd\_io\_\_bi\_t  
gf180mcu\_fd\_io\_\_bi\_24t

with the project itself driving the I/O-cell control terminals appropriately.

The integration system must not synthesize output-enable logic on behalf of the project.

# **7\. Pad control signals belong to the project**

All functional and control inputs of an I/O cell are the responsibility of the project.

Examples include, where applicable:

OE  
IE  
PU  
PD  
CS  
SL  
PDRV0  
PDRV1

and similar I/O-cell control terminals.

The integration layer must not automatically generate:

* pull-up control logic;  
* pull-down control logic;  
* output-enable control;  
* input-enable control;  
* slew-rate control;  
* drive-strength control;  
* POR control;  
* startup logic.

The generated project DEF must expose the necessary project-side connections for the chosen physical I/O cell.

The exact terminal list must come from the authoritative GF180 cell definitions.

# **8\. Analog pins**

An analog pin requires only its name and `io_type: analog`:

\- name: ain  
  io\_type: analog

The `secondary_esd` field is no longer required and does not affect the
perimeter I/O cell, which remains `gf180mcu_fd_io__asig_5p0`. For compatibility
with existing project metadata, an optional boolean `secondary_esd` field may
be present on an analog pin; it is retained as inert metadata and must not
change validation, pad mapping, or analog pin categorization. The field is not
valid on non-analog pin types.

# **9\. Power architecture**

The intended system architecture is:

* common chip-wide VSS/ground;  
* independent positive supply domains for individual projects or project blocks.

The purpose of separate positive supplies is to prevent a failed or shorted project from disturbing the other projects on the die.

Do not assume a common VDD or VDDIO across all projects.

Shared digital inputs across independently powered project domains are therefore not part of the baseline architecture.

# **10\. Physical die padframe**

The die has 88 physical I/O positions, with 22 pads per side.

Use immutable physical slot names.

N01 ... N22  
E01 ... E22  
S01 ... S22  
W01 ... W22

For the left and right sides, numbering is explicitly defined from bottom to top:

W01 \= bottom-most left pad  
W22 \= top-most left pad

E01 \= bottom-most right pad  
E22 \= top-most right pad

The top/bottom numbering direction should likewise be explicitly documented in the physical template rather than inferred from clockwise/counter-clockwise terminology.

Physical slot names should remain constant even if the cell occupying the slot changes.

For example, W13 always means the same physical pad location.

# **11\. Dynamic power/ground groups**

E11, E12, W11, and W12 are ordinary project pad slots. They have no default or
fixed ground-pad assignment, are not owned by the integration template, and
may receive any participant pad type permitted at another ordinary slot. No
invariant break is attached to any of these four locations.

Every allocated project I/O sequence is enclosed by breaks: one break occurs
immediately before its first I/O cell and another immediately after its last
I/O cell. Within that sequence, pads are scanned in physical allocation order
while maintaining a power count and a ground count for the current group. A
break is inserted immediately before a power pad if the group already contains
one power pad, or immediately before a ground pad if the group already contains
one ground pad. Inserting any break resets both counts to zero before processing
the next pad.

Consequently every group delimited by breaks must contain exactly one power pad
and exactly one ground pad. Signal and analog I/O cells may occur anywhere
between them. For example:

```text
BREAK, ground, ..., power, ..., BREAK, power, ..., ground, ..., BREAK
```

Generation must fail if the first/last boundaries or the repeated-supply rule
would leave a completed group without exactly one power and one ground pad.
DVDD pads inherently divide the VDD/DVDD rails into distinct electrical
segments.

Additional gf180mcu\_fd\_io\_\_brk5 cells are generated dynamically:

* immediately before the first I/O pad allocated to each project;
* immediately after the final I/O pad allocated to each project;
* immediately before a power pad when the current region already contains a
  project power pad;
* immediately before a ground pad when the current region already contains a
  project ground pad.

All break placement belongs in the template-transformation logic because
project extent and participant power/ground-pad order come from info.yaml. A break cell
is auxiliary physical geometry: it is not one of the 88 immutable I/O slots, it
does not consume a participant pin, and it must not appear in the pad-center CSV.

At every fixed or dynamic break boundary, `brk5` replaces all `fill5` filler
cells in that gap; it is not added alongside them. The padring configuration
language provides separate default filler families and a parameterless break
directive. The resulting sequence is conceptually:

```text
FILLER gf180mcu_fd_io__fill5 ;
BREAKFILLER gf180mcu_fd_io__brk5 ;

PAD <preceding-io> ... ;
BREAK ;
PAD <following-io> ... ;
```

`BREAK ;` preserves the ordinary automatically calculated gap width but fills
the complete gap using only the `BREAKFILLER` family. It must not introduce an
additional cell or fixed space, and no default `FILLER` cell may occur in that
gap. For example, a 25-micron gap uses five `brk5` cells.

# **12\. Quadrant placement**

Projects are placed in die quadrants.

Each block type has a canonical floorplan.

A participant implements against the canonical floorplan. A final project placement elsewhere on the die is a geometric transformation of that canonical placement.

This allows the same project layout to be placed in multiple legal locations while preserving alignment between:

* project-side interface;  
* I/O cells;  
* final padframe.

The canonical user DEF should be generated as though the project occupies the designated canonical position, currently the upper-left valid location.

# **13\. Project block types**

Current planned block configurations are:

| Block | Area | Physical pad/interface sites | Possible locations |
| :---- | :---- | :---- | :---- |
| A | 1/4 die | 22 | 4 |
| B | 1/8 die | 16 | 4 |
| C | 1/8 die | 6 | 8 |
| D | 1/16 die | 10 | 4 |
| E | 1/16 die | 6 | 8 |

Every listed physical pad/interface site is available for participant mapping,
including power and ground pads declared by the participant. There are no
template-reserved VSS sites to subtract from a block's pin capacity.

The exact allocation for each block type should be encoded explicitly rather than inferred mathematically.

# **14\. Canonical A-block pin sequence**

The definitive A variant has these 22 ordered participant-configurable
positions:

```text
A_SLOTS = [
    "W12", "W13", "W14", "W15", "W16", "W17",
    "W18", "W19", "W20", "W21", "W22",
    "N01", "N02", "N03", "N04", "N05", "N06",
    "N07", "N08", "N09", "N10", "N11",
]
```

The exact edge numbering direction is fixed by the production 88-pad template.
The corresponding configurable lists for every other definitive variant are
the complete ordered slot lists in section 24.1. These lists are authoritative
for padring generation; no fixed supply positions are removed from them.

# **15\. YosysHQ padring configuration**

Use the existing padring .cfg itself as the physical template.

Do not create a redundant YAML representation of:

* chip AREA;  
* GRID;  
* corners;  
* pad side;  
* pad ordering;  
* FLIP;  
* spacing;  
* fillers.

The template already contains this information.

Example:

DESIGN workshop\_padring;

AREA 2935 2935;

GRID 0.005;

CORNER CORNER\_1 SE gf180mcu\_fd\_io\_\_cor ;  
CORNER CORNER\_2 SW gf180mcu\_fd\_io\_\_cor ;  
CORNER CORNER\_3 NE gf180mcu\_fd\_io\_\_cor ;  
CORNER CORNER\_4 NW gf180mcu\_fd\_io\_\_cor ;

FILLER gf180mcu\_fd\_io\_\_fill5 ;

PAD N01 N gf180mcu\_fd\_io\_\_asig\_5p0 ;  
PAD N02 N gf180mcu\_fd\_io\_\_asig\_5p0 ;  
...

The template is the physical source of truth.

The production template must contain exactly 88 ordinary PAD entries: N01...N22,
E01...E22, S01...S22, and W01...W22. Legacy PAD entries must not be retained in
addition to these slots. YosysHQ padring does not support LOC directives; the
side is specified directly on each PAD line.

# **16\. Template transformation**

The converter should:

1\. Read the original padring .cfg.

2\. Preserve all comments and physical directives whenever possible.

3\. Locate physical slots belonging to the selected project block.

4\. Assign info.yaml pins to those slots using the predefined ordered slot sequence.

5\. Replace the cell type at those positions.

6\. Keep the pad instance name equal to its canonical physical slot name.

7\. Leave all unrelated padframe cells untouched.

8\. Convert unused project signal slots into analog placeholder pads.

The converter should preserve:

AREA  
GRID  
CORNER  
FILLER  
SPACE  
FLIP

and all fixed PAD entries.

For a production template, "unrelated" fixed PAD entries means the immutable
slots outside the selected project block. Unexpected legacy PAD instance names
are an error because they add physical pads beyond the canonical 88 positions.

# **17\. Canonical padring names and project names**

The physical slot identity is also the canonical padring PAD instance name.

Example:

physical slot: W13  
user pin:      reset\_n  
cell:          gf180mcu\_fd\_io\_\_in\_s

The generated padring directive is:

PAD W13 W gf180mcu\_fd\_io\_\_in\_s ;

while the integration metadata records:

slot: W13  
instance: W13
pin\_name: reset\_n
cell: gf180mcu\_fd\_io\_\_in\_s

The padring DEF, GDS, and Verilog use canonical names. Team-code prefixes and
participant pad names are applied only when the padring and projects are
instantiated in the future top-level design. The pad-center CSV reports the
intended top-level project pin name, including its team-code prefix, as mapping
metadata; this does not rename objects inside the padring layout.

# **18\. User-area-facing pin names**

Each project-facing I/O-cell terminal in the padring DEF and Verilog is named
`<canonical-pad>_<cell-terminal>`, for example `W01_A` or `W01_Y`. The DEF PINS
geometry is derived from the corresponding transformed LEF pin rectangles so
that the same named terminals exist in the generated layout.

The corresponding pin in a user project DEF is based on the participant pin
name and I/O-cell terminal, for example `RST_A`. A scalar name may be sanitized
as required for a DEF/Verilog identifier, and collisions after sanitization are
errors. A valid bus suffix must not be flattened or replaced with underscores.
Terminal qualifiers are inserted before the bus suffix. For example, the
control terminals for participant pin `DATA[0]` are `DATA_PU[0]`, `DATA_PD[0]`,
and so on, not `DATA_0_PU` or `DATA_0__PU`. The generated DEF declares
`BUSBITCHARS "[]"` and retains the bracketed form.

# **19\. Unused project slots**

If a project uses fewer signal pins than its block permits, the remaining allocated I/O positions should use:

gf180mcu\_fd\_io\_\_asig\_5p0

Example:

PAD W18 W gf180mcu\_fd\_io\_\_asig\_5p0 ;

These placeholder analog cells:

* maintain the physical padframe geometry;  
* are not project-visible signals;  
* do not require secondary\_esd;  
* do not appear as user pins.

They are physical placeholders only.

# **20\. Power and ground cells are not unused placeholders**

Participant pads declared as VDD/VSS must use proper power cells such as:

gf180mcu\_fd\_io\_\_dvdd  
gf180mcu\_fd\_io\_\_dvss

They must not be replaced with analog placeholders. Power and ground locations
come exclusively from the participant pad mapping; the project-block template
does not preassign them.

# **21\. Padring LEF files**

The Makefile invoking padring must provide LEFs for every pad cell the generator may emit.

At minimum, based on the current pin types:

gf180mcu\_fd\_io\_\_cor.lef  
gf180mcu\_fd\_io\_\_fill5.lef
gf180mcu\_fd\_io\_\_brk5.lef
gf180mcu\_fd\_io\_\_bi\_t.lef  
gf180mcu\_fd\_io\_\_bi\_24t.lef  
gf180mcu\_fd\_io\_\_in\_c.lef  
gf180mcu\_fd\_io\_\_in\_s.lef  
gf180mcu\_fd\_io\_\_asig\_5p0.lef  
gf180mcu\_fd\_io\_\_dvdd.lef  
gf180mcu\_fd\_io\_\_dvss.lef

A Makefile invocation therefore needs entries such as:

\--lef $(TECH\_PDK)/libs.ref/gf180mcu\_fd\_io/lef/gf180mcu\_fd\_io\_\_cor.lef \\  
\--lef $(TECH\_PDK)/libs.ref/gf180mcu\_fd\_io/lef/gf180mcu\_fd\_io\_\_fill5.lef \\
\--lef $(TECH\_PDK)/libs.ref/gf180mcu\_fd\_io/lef/gf180mcu\_fd\_io\_\_brk5.lef \\
\--lef $(TECH\_PDK)/libs.ref/gf180mcu\_fd\_io/lef/gf180mcu\_fd\_io\_\_bi\_t.lef \\  
\--lef $(TECH\_PDK)/libs.ref/gf180mcu\_fd\_io/lef/gf180mcu\_fd\_io\_\_bi\_24t.lef \\  
\--lef $(TECH\_PDK)/libs.ref/gf180mcu\_fd\_io/lef/gf180mcu\_fd\_io\_\_in\_c.lef \\  
\--lef $(TECH\_PDK)/libs.ref/gf180mcu\_fd\_io/lef/gf180mcu\_fd\_io\_\_in\_s.lef \\  
\--lef $(TECH\_PDK)/libs.ref/gf180mcu\_fd\_io/lef/gf180mcu\_fd\_io\_\_asig\_5p0.lef \\  
\--lef $(TECH\_PDK)/libs.ref/gf180mcu\_fd\_io/lef/gf180mcu\_fd\_io\_\_dvdd.lef \\  
\--lef $(TECH\_PDK)/libs.ref/gf180mcu\_fd\_io/lef/gf180mcu\_fd\_io\_\_dvss.lef

# **22\. Typical padring invocation**

Current style:

$(BIN\_DIR)/padring \-v \\  
    \--lef .../gf180mcu\_fd\_io\_\_cor.lef \\  
    \--lef .../gf180mcu\_fd\_io\_\_fill5.lef \\
    \--lef .../gf180mcu\_fd\_io\_\_brk5.lef \\
    \--lef .../gf180mcu\_fd\_io\_\_bi\_t.lef \\  
    \--lef .../gf180mcu\_fd\_io\_\_asig\_5p0.lef \\  
    \--lef .../gf180mcu\_fd\_io\_\_dvdd.lef \\  
    \--lef .../gf180mcu\_fd\_io\_\_dvss.lef \\  
    \--svg $(PADRING\_DIR)/outputs/workshop\_padring.svg \\  
    \--def $(DEF) \\  
    \--ver $(PADRING\_DIR)/outputs/workshop\_padring.v \\
    $(PADRING\_DIR)/workshop\_padring.cfg \\  
    | tee $(PADRING\_DIR)/workshop\_padring.log

Add additional LEFs whenever additional io\_type mappings can emit those cells.

# **23\. Generated pad mapping metadata**

In addition to the .cfg, generate a machine-readable mapping.

Example:

pads:  
  \- pin\_index: 0  
    pin\_name: reset\_n  
    slot: W13  
    instance: W13
    io\_type: input\_schmitt  
    cell: gf180mcu\_fd\_io\_\_in\_s

  \- pin\_index: 1  
    pin\_name: "data\[7\]"  
    slot: W14  
    instance: W14
    io\_type: bidirectional  
    cell: gf180mcu\_fd\_io\_\_bi\_t

  \- slot: W20  
    instance: W20
    generated: true  
    cell: gf180mcu\_fd\_io\_\_asig\_5p0

This mapping becomes the connection between:

info.yaml  
      ↓  
physical pad slots  
      ↓  
padring configuration  
      ↓  
padring-generated DEF  
      ↓  
project virtual-padframe DEF  
      ↓  
final chip integration

Both YAML and JSON serializations may be generated. The JSON form is used by
the KLayout conversion stage without requiring a YAML package in KLayout's
embedded Python environment.
The mapping records the team code supplied by the project-ID Make target so
downstream outputs can construct globally unique project pin names.

# **24\. Generated project DEF / virtual padframe**

The project DEF is not merely documentation.

It is an input physical constraint to the participant's implementation flow.

It defines the project-side connection locations associated with its I/O cells.

The participant places and routes the project against those locations.

Therefore, when the completed block is inserted into the final chip:

* project-side interfaces already align;  
* no project I/O rerouting should be necessary;  
* placement transformation is deterministic.

Project DEF pins use the participant's pin name followed by one underscore and
the I/O-cell terminal name, such as `RST_A`. The mapping metadata for every
such pin also records its canonical padring slot.

Analog project pins are the exception: the project DEF pin uses the user pad
name from info.yaml without an `_ASIG5V` suffix and uses only the inward-facing
Metal2 ASIG5V geometry. Analog pads requesting secondary ESD will be handled by
a later, separately specified integration stage.

Mapped power and ground pads also use their user names from info.yaml. Their
project DEF pins use the inward-facing Metal2 geometry of the corresponding
DVDD or DVSS padring pin.

The canonical DEF represents the project in its canonical upper-left
placement. Its local lower-left coordinate is always `(0, 0)`. Only this
canonical placement is generated in the current task; transforms to other
legal top-level locations remain future work.

The generator inputs are:

* the padring-generated top-level DEF;
* pad mapping metadata;
* one selected block type;
* the canonical block lower-left origin in top-level coordinates;
* block width and height;
* GF180 I/O LEFs used to associate named terminals with pin geometry and layers.

Origin, width, and height CLI values are specified in microns. The generated
DEF inherits the padring DEF `UNITS DISTANCE MICRONS` value. Every micron input
must convert exactly to an integer number of inherited DEF database units;
non-grid-aligned values are rejected rather than rounded.

The generated DIEAREA is exactly:

```text
DIEAREA ( 0 0 ) ( <block-width-dbu> <block-height-dbu> ) ;
```

For every mapped user pad, the generator emits every project-facing I/O-cell
terminal. Input-pad `Y` terminals use the bare user pad name, such as `RST` or
`DATA[0]`, rather than `RST_Y` or `DATA_Y[0]`. For bidirectional pads, `Y` uses
`<user-pad-name>_IN` and `A` uses `<user-pad-name>_OUT`. For a bus pin, these
qualifiers precede the index: `DATA_IN[0]` and `DATA_OUT[0]`. Other digital
terminals retain `<user-pad-name>_<cell-terminal>`, such as `RST_PU`,
`DATA_PU[0]`, `DATA_PD[0]`, or `DATA_OE[0]`. Directions are inverted relative
to the padring-facing terminal, while DEF `USE` and other applicable pin
properties are preserved.

More generally, a participant name is separated into its scalar base and its
trailing bracketed bus suffix. The terminal qualifier is appended to the base,
then the unchanged suffix is restored. This applies to every trailing index in
a multidimensional name: `DATA[3][0]` with terminal `PU` becomes
`DATA_PU[3][0]`. Malformed or non-trailing bracket syntax is rejected rather
than silently flattened.

## **24.0.1 Project-layout text case resolution**

The default spelling of a generated project-interface signal uses the
participant pin-name case from info.yaml and the authoritative I/O-cell
terminal case. For example, pad `DATA` with terminals `OE` and `IE` defaults to
`DATA_OE` and `DATA_IE`. The same rule applies before a bus suffix, producing
names such as `DATA_OE[0]`.

Before finalizing project-interface names, the generator examines text objects
placed directly in the top cell of the project GDS selected by `LAYOUT_FILE`.
Text in referenced lower-level cells is not a top-level signal declaration and
is ignored. Direct top-cell text may be considered on any GDS layer because a
candidate is relevant only when its complete logical name matches an expected
generated signal case-insensitively.

For each expected signal:

* if no top-cell text matches case-insensitively, use the default spelling;
* if one exact spelling matches, use that complete layout spelling in the
  generated project DEF and every corresponding generated Verilog interface;
* repeated text objects with the same exact spelling are permitted;
* two or more distinct spellings that compare equal case-insensitively are a
  conflict and generation must fail.

Thus, top-cell text `DATA_oe` overrides default `DATA_OE`, and `DATA_ie`
overrides `DATA_IE`. The generated DEF and corresponding Verilog pins must then
use `DATA_oe` and `DATA_ie` exactly. If both `DATA_OE` and `DATA_oe` occur in
the top cell, generation fails rather than choosing one. Bus spelling is
handled identically: layout text `DATA_oe[0]` overrides default `DATA_OE[0]`
without flattening or moving the index.

Case-insensitive collisions between two different expected generated signals
are also errors, even if the layout contains no matching text. Interface
mapping metadata records both the default generated name and the final
layout-resolved spelling so that DEF, Verilog, and layout-name audits use the
same decision.

Pin rectangles come from the actual named PINS geometry in the generated
padring DEF. They must not be positioned again from I/O-cell placement or LEF
macro dimensions. LEF terminal definitions identify which padring geometry is
project-facing and which routing layer belongs to the terminal. All qualifying
rectangles remain separate and stay on their original routing layers. Their
span parallel to the DIEAREA boundary is preserved except where it must be
clipped to the local DIEAREA; their span normal to the boundary is replaced by
the inward stub described below.

All innermost rectangles for a selected terminal must share the same inward
boundary coordinate: X for west/east pads and Y for north/south pads. That
coordinate must equal the corresponding user-block DIEAREA boundary. A missing,
non-unique, or mismatched boundary is an error. The project DEF omits every
portion of the terminal outside the user DIEAREA. After creating the 1.0 micron
inward stub, intersect the rectangle with the complete local DIEAREA in both X
and Y. This clips an edge pad's orthogonal extent when it crosses an adjacent
block boundary; for example, an N06 Metal2 rectangle extending left of an EV
or CV block begins at local X zero. Emit each clipped rectangle only when its
intersection has positive width and height; discard an individual rectangle
whose intersection is empty. The inward stub is:

* west-side pads: boundary X to boundary X + 1 micron;
* east-side pads: boundary X - 1 micron to boundary X;
* north-side pads: boundary Y - 1 micron to boundary Y;
* south-side pads: boundary Y to boundary Y + 1 micron.

The extension is converted to inherited DEF database units. For example, each
W13 DVDD project rectangle begins at local X 0 and ends at local X 1.0 micron,
while retaining its original Y interval.

Extended top-level coordinates are translated to project-local coordinates:

```text
user_x = top_x - block_origin_x
user_y = top_y - block_origin_y
```

The mapping output preserves, for each generated rectangle:

* user pin name and I/O-cell terminal;
* padring instance;
* canonical physical pad slot;
* routing layer and inherited DEF properties;
* original top-level rectangle;
* extended top-level rectangle;
* clipped and translated user rectangle.

Generation fails if a mapped padring instance or required named pin is absent,
the routing layer is unknown, the project-facing boundary is ambiguous, a
micron input is not exactly representable in inherited DBU, or clipping leaves
no positive-area rectangle for the selected terminal inside the local DIEAREA.
A rectangle that crosses a DIEAREA boundary is clipped, and an individual
rectangle wholly outside it is omitted; neither condition is itself an error
when another rectangle for that terminal remains.

## **24.1 Canonical user-block slot allocations**

The repository-root `blocks.data` tab-separated table is the authoritative
source for DEF block variants. Load `Blocks`, `Pin order`, `Placement`,
`width`, `height`, `Area`, and `block` at process startup, without hardcoded
geometry fallbacks. Package the same table for installed commands.
Padframe configuration and project DEF generation must use the same shared
loader and slot allocations; no separate hardcoded padframe slot table is allowed.

Pin ranges are inclusive, concatenated left to right, and may descend.
`Pin order` determines the available slots. `Pin count` is required and must
equal the number of expanded slots; mismatches and duplicate slots are errors.
`utilization` is descriptive.
Origins, dimensions, and blockage coordinates are exact decimal microns.
`Area`, including thousands separators, is the authoritative selection metric.
The `block` column contains local rectangles `x1,y1-x2,y2` separated by `&`;
a blank value means no blockages. Each rectangle creates placement and
routing blockages on all routing layers. Do not retain the former hardcoded
Metal2 corner blockages absent from the table. Invalid tables fail explicitly.

Generated DEF blockage rectangles use standards-compliant syntax with no `+`
before `RECT`:

```def
- PLACEMENT RECT ( x1 y1 ) ( x2 y2 ) ;
- LAYER Metal2 RECT ( x1 y1 ) ( x2 y2 ) ;
```

The `+` prefix is reserved for blockage options such as `SPACING`,
`DESIGNRULEWIDTH`, `PUSHDOWN`, `SOFT`, or `PARTIAL`; it must not prefix the
`RECT` geometry token.

The following allocation constraints are part of variant eligibility:

* for EV and CV, the first allocated participant I/O must have `io_type` power
  or ground;
* for EH and CH, the last allocated participant I/O must have `io_type` power
  or ground.

“First” and “last” refer to participant pins in their allocation order, not to
unused analog placeholder cells. Automatic configuration selection excludes a
variant whose endpoint constraint is not satisfied. Explicitly requesting such
a variant is an error.

Configuration selection considers project width, height, required pin count,
and the endpoint power/ground constraints above. Generate every qualifying
non-dominated minimum-area configuration.
Do not emit a larger-area configuration when a smaller-area configuration
fits (for example, do not generate ACV when A fits). Preserve distinct
equal-area placements such as EV and EH when both qualify. Project dimensions
must fit the usable geometry described by the selected variant, and required
pins must not exceed its pin count.

The implemented CLI command is `generate-project-def`. It accepts
`--project-width` and `--project-height` in microns, plus the padring DEF,
mapping metadata, and I/O LEFs. Without `--variant`, it writes one DEF and one
interface-mapping YAML for every minimum-area fitting variant into
`--output-dir`. With `--variant CODE`, it writes only that requested variant
after verifying that it fits. The default GF180 routing-blockage layer list is
Metal1 through Metal5 and can be overridden by repeating `--routing-layer`.

The normal Makefile flow derives project width and height from the project's
GDS rather than requiring manual dimensions. The GDS must have exactly one top
cell. The `pr_boundary` layer is not used to derive project dimensions because
it can describe the selected block size rather than the minimum physical size
of the project content.

Classify project type using only cells that contain direct physical geometry on
the PDK-mapped poly, diffusion, or MIM-cap layers. Ignore cells with no geometry
on those device layers, including via-only cells and metal-only routing cells.
A project is digital when all relevant device-layer cells are macros from the
configured standard-cell LEFs, analog when none are standard cells, and mixed
signal when both standard-cell and non-standard device geometry are present.
Standard-cell membership and tap/fill classification come from those LEFs, not
from guessed cell-name prefixes. Calculate physical size independently of the
project-type result. Always include the transformed GDS geometry of every
recognized standard-cell instance, across all GDS layers, except for the
excluded standard-cell classes and families below. Exclude fillers,
spacers, well taps, endcaps, antenna cells, tie-high/tie-low cells, buffers,
clock buffers, tri-state buffers, and delay-buffer cells from this boundary.
Also always include transformed non-standard GDS geometry on the PDK-mapped
poly, diffusion, and mim_cap layers. The computed boundary is the union of
these two geometry sets regardless of whether classification reports digital,
analog, mixed signal, or unknown. Geometry in any cell whose name contains `secondary_esd`
(case-insensitive), including its hierarchy, is excluded from this analog
measurement. The semantic layers must be resolved through the configured PDK
KLayout layer-properties file; do not substitute `pr_boundary` or hard-code
another layer as a fallback.

For each digital candidate, first accept when measured width and height both
fit the variant dimensions. If either dimension exceeds its limit, accept only
when measured bounding-box area (`width_microns * height_microns`) is strictly
less than 90% of the variant's usable `Area` from `blocks.data`. Exactly 90%
does not qualify for the fallback. Pin capacity and endpoint rules always apply;
select every qualifying variant with minimum usable area. Analog, mixed-signal,
unknown, and unclassified inputs retain dimension checks only. Automatic
selection and explicit variant validation use the same policy.

After selecting minimum-area variants, if D is selected, additionally generate
the smallest strictly larger usable-area fitting E-family variants. If a
B-family variant is selected, likewise add the smallest strictly larger fitting
C-family variants. Include all ties without duplicates. Alternatives must
satisfy the same size, pin-capacity, and endpoint criteria; if none fit, retain
the corner selection alone. Automatic builds emit DEFs and interface maps for
these additional variants too. Explicit `--variant` remains a single selection.
When the minimum-area selection includes BVE or BHE, also generate A if A
passes the same size, pin-capacity, and endpoint checks. Add it once, in
addition to any fitting C-family alternatives.

Use the measured physical bounding-box width and height, converted to microns
using the GDS layout DBU, together with the participant pin count from
info.yaml to select every minimum-area fitting variant before its
variant-specific padring and project DEF are generated. Fail if the required
standard-cell library or layer mapping is unavailable, or if the selected
geometry has no positive-area bounding box. An unknown type does not invalidate
an otherwise valid physical-size measurement. The PR-boundary layer
remains available for independent placement/containment validation; it does
not determine the minimum-fitting variant.

Independently of this sizing measurement, require exactly one rectangular
PR-boundary shape and no other shapes directly in the project top cell on the
configured PR-boundary layer/datatype (default `0/0`). Record its geometry for
full-chip placement validation; do not use it to determine the project DEF
dimensions.

The GDS filename must be selected from the expanded `LAYOUT_FILE` in
`<DATA_ROOT>/<team-code>/<project.lvs_config>`; selecting the first file in a
directory is prohibited. Info, LVS configuration, GDS, and netlists preserve
their repository-relative paths beneath `<DATA_ROOT>/<team-code>`. The PDK
layer-properties file and standard-cell LEFs used for the measurement must
come from the configured `TECH_PDK`.

Mapped slots must be an ordered subsequence of the variant's authoritative pin
order. All listed slots, including E11, E12, W11, and W12, are ordinary
participant-mappable positions and count toward the variant's full capacity.

# **25\. Transforming projects to other quadrants**

Final integration may place the project in another legal quadrant.

This should be implemented as a known geometric transformation of the canonical block.

Potential transformations include:

translation  
rotation

and reflection only if the physical/library rules explicitly allow it.

A transformation must consistently apply to:

* project GDS;  
* project DEF geometry;  
* interface locations;  
* pad-slot mapping.

Do not independently recompute pin ordering after placement.

Instead, derive each legal block placement from the canonical ordered slot map.

# **26\. Package bonding**

The die contains 88 pad positions while the target package has 64 pins.

Two bonding configurations are planned using diagonally opposite portions of the die.

The concepts discussed are:

top-left \+ bottom-right  
bottom-left \+ top-right

Package pin numbers must be kept separate from physical die pad numbers.

Do not overload names such as L13 to mean a package pin.

Maintain separate data such as:

die\_slot: W13  
package\_pin: 27

when package mapping is added.

# **27\. Download script behavior**

The package-provided download command is `chipathon-download-data`, implemented
inside `chipathon2026-system` by the
`chipathon2026_integration.fullchip_lvs_cli:download_main` entry point. It is
not a program in the parent directory.

The command automatically writes a legacy-compatible `data_root/download.report`
alongside `download_manifest.json`. `--report PATH` overrides the report path.
Each successful `TEAM: repository` record contains indented `info.yaml`,
`TOP_LAYOUT`, and `GDS saved` fields, with project-relative artifact paths and the
resolved layout top name. Failed records contain `ERROR` and no `GDS saved`.
Records follow repository-list order, including failures, followed by
`Successful` and `Failed` counts. Every record includes `info.yaml` with the
project-relative saved path (including retained failed-project paths), or `none` when
no file was saved. The report is written for partial-success runs too; report-write
failure makes the command fail. Progress logs remain separate from the report.
Each record includes `lvs_config` and `export_control` lines with project-relative
downloaded paths. Export control is `none` if undeclared and
`NOT FOUND: filename` if declared but unavailable; such a download failure
still fails the project. Failed records retain available paths, including
relocated LVS configurations. Unread metadata uses `unknown`, invalid export
declarations use `invalid: value`, and missing declared LVS configurations
use `NOT FOUND: filename`.
Saved file fields in the report use `project-relative-path: sha256`, where `sha256`
is the saved file's 64-digit hexadecimal SHA-256 digest. Status values have no
checksum. The audit parser must support both this form and legacy plain paths.
Paths are relative to `data_root/TEAM` (or `TEAM_failed` for failed projects).
A `Data root` header gives the data root relative to the report directory;
shared export documents use `../export_control/filename`.
The detailed report contract is specified in integration_spec.md section 19.3.

For every repository-list project, inspect the optional
`project.export_control` key in `info.yaml`. When present, it must name one
nonempty, safe repository-relative document path. For both this key and
`project.lvs_config`, accept a leading `$UPRJ_ROOT/` or `${UPRJ_ROOT}/` as the
repository root and remove it before path resolution. No other unresolved
variable is allowed. Download that document from
the same immutable project revision as the other artifacts and save it as:

```text
<data_root>/export_control/<TEAM>_<source-basename>
```

An absent key requires no export-control download. An invalid value or missing
document is an individual-project failure. If the export-control document was
successfully downloaded before a later project failure, retain it for
debugging and record its source and destination in the download manifest.

When an individual project fails after its `<data_root>/<TEAM>` directory has
been created, do not delete that directory. Atomically rename it to
`<data_root>/<TEAM>_failed` so its partial files remain available for
debugging, record that path in the failure manifest entry, and continue with
the other projects. Integration must only consume the unsuffixed successful
team directory. A failure before team-directory creation does not require an
empty `_failed` directory. Any separately collected export-control document
for a failed project is retained.

The project download flow currently retrieves:

info.yaml  
    ↓  
project.lvs\_config  
    ↓  
lvs\_config.json  
    ↓  
LAYOUT\_FILE  
    ↓  
GDS

In `lvs_config.json`, treat a filename beginning with `./` as rooted at the
participant repository: `./path/file` is equivalent to
`$UPRJ_ROOT/path/file`. Apply this normalization to `LAYOUT_FILE` and all
participant LVS SPICE and Verilog source-file fields. Do not implicitly convert
bare relative paths that lack the leading `./`.

`LVS_SPICE_FILES_TO_FIX` is deprecated and must not contain any nonempty file
name. Reject a project during download or LVS-environment generation when the
field is nonempty. Accept a missing key, empty string, or empty list, and omit
the deprecated field from generated configurations.

When saving downloaded metadata such as info.yaml, stale files should not silently be reused.

The current command style is:

./download\_chipathon\_gds\_v2.py \\  
    \--info-dir info \\  
    \--gds-dir gds \\  
    \--overwrite \\  
    repo.list 2\>&1 | tee download.report

Because Python may buffer stdout when piping through tee, use:

sys.stdout.reconfigure(line\_buffering=True)

# **28\. Validation requirements**

The converter should reject:

* missing pins;  
* malformed YAML;  
* unsupported io\_type;  
* duplicate user pin names;  
* malformed optional secondary\_esd values on analog pins;
* secondary\_esd on non-analog pins;  
* more project pins than available block slots;  
* duplicate generated padring instance names;  
* collisions after pin-name sanitization;  
* missing physical slots in the padring template;  
* unexpected legacy PAD instances in a production template;
* a missing break before the first or after the last allocated project I/O cell;
* a power/ground group containing anything other than exactly one power and one ground pad;
* a missing break before a second power or second ground pad in a group;
* missing required cell mappings.

It should also verify that every physical project slot is accounted for as one of:

* user I/O;
* participant power;
* participant ground;
* unused analog placeholder.

# **29\. Important implementation principle**

There should be a single source of truth for physical geometry.

The production YosysHQ padring template should define:

* pad positions;  
* sides;  
* ordering;  
* corner cells;  
* filler cells;  
* power/ground positions;  
* orientation;  
* FLIP;  
* area;  
* grid.

Python configuration should define only higher-level semantic relationships such as:

A block uses slots:  
W13, W14, ..., N11

Do not duplicate all physical padring geometry in another YAML file.

# **30\. Items still to finalize**

The next implementation work should explicitly define:

1\. transformations from canonical placement to each legal non-canonical physical location;

2\. secondary-ESD cell layout, abstract, connectivity, and placement policy for analog pads;

3\. any additional block variants beyond the explicit allocations in section 24.1;

4\. package-bond mappings from 88 die pads to the two 64-pin package configurations.

# **31\. Project-ID padframe build and generated artifacts**

The repository provides chipathon2026-system/Makefile.padframe. From the
repository root, a project padframe is built by project ID:

make \-f chipathon2026-system/Makefile.padframe A01

The default input is:

data/A01/info.yaml

DATA\_ROOT and all build/tool/PDK paths must remain overrideable Make variables.
DEF\_DBU defaults to 0.005 microns and must remain overrideable. The generated
DEF therefore uses UNITS DISTANCE MICRONS 200, and the KLayout import/output DBU
must be set to the same value.
When assembling a combined chip from project GDS files, each source GDS must be
read into its own `pya.Layout` so its declared source DBU is preserved. The
destination chip layout is created from the integrated DEF at 0.005 microns per
database unit (`UNITS DISTANCE MICRONS 200`). Copy each source hierarchy into
its DEF-created destination project cell with KLayout `copy_tree`, allowing
KLayout to convert between source and destination DBUs. Do not read project GDS
files directly into the destination layout, assume a common source DBU,
manually rescale integer geometry, or create a duplicate project placement in
addition to the DEF component.
When the integrated GDS adds top-level project-pin text from the authoritative
pin CSV, suppress or remove text created by the DEF reader on that label layer
first. The final top cell must contain exactly one label per CSV row at the CSV
pad-center coordinate, not both a DEF-derived label and a CSV-derived label.
PAD\_MARKER\_LAYER and PAD\_MARKER\_DATATYPE must also be overrideable Make
variables. Their GF180 defaults are 37 and 0 respectively; other processes may
identify the physical pad-center marker on a different GDS layer/datatype pair.
When the repository-local padring executable is selected, the Makefile must
rebuild it with CMake whenever its C++ source or header files change. This
prevents newly added command-line options from being passed to a stale binary.
The project-ID target performs these stages in order:

1\. generate-padring;

2\. run-padring;

3\. KLayout GDS assembly using Workshop\_CASS/def2stream.py.

The default project output directory is build/padframes/A01 and contains:

```text
A01_padring.cfg
A01_pad_map.yaml
A01_pad_map.json
A01_padring.def
A01_padring.svg
A01_padring.v
A01_padring.gds
A01_pads.csv
```

The generated padring top-cell name and structural Verilog module name are
`<team-code>_padring`. For example, project A01 generates `A01_padring` in the
CFG, DEF, GDS, and Verilog. The Makefile passes the same derived design name to
KLayout; `DESIGN_NAME` remains overrideable when required.

The Verilog output is a structural padring module whose I/O-cell instances use
canonical physical-slot names. It exposes the project-facing cell terminals as
module ports named `<canonical-pad>_<cell-terminal>`. Break, filler, corner,
and unused placeholder cells do not create module ports.

The structural padring Verilog also exposes every physical pad using its bare
canonical slot name, such as `W01`, `W11`, or `W12`. A digital I/O cell's `PAD`
terminal connects to that canonical port. Analog pad connectivity is aliased to
the canonical port with a continuous `assign` statement where a separate
canonical-prefixed project-facing terminal is also exposed. Canonical DVSS pad
ports are likewise shorted with continuous assignments rather than Verilog
`tran` primitives.

The padring DEF must expose the same bare canonical physical-pad ports. Every
canonical slot, including unused placeholders, has an `INOUT` pin rectangle on
Metal5 at the transformed physical pad location. The common GF180 Metal5 `PAD`
rectangle defines this pad-location geometry for cell variants whose LEF does
not separately name the physical pad metal.

A canonical DVDD or DVSS pad pin additionally includes all transformed Metal2
rectangles from that cell's corresponding `DVDD` or `DVSS` LEF terminal. These
Metal2 shapes face the user area and allow top-level routing to the project.
DVDD canonical pins use `USE POWER`; DVSS canonical pins use `USE GROUND`.
Their Metal5 and Metal2 shapes belong to the same canonical DEF pin/net.

The structural Verilog must instantiate every component emitted in the
padring DEF, not only the 88 canonical I/O cells. GF180 filler and corner cells
contain devices and therefore cannot be omitted from LVS. Each `fill5` and
corner instance connects its available `VSS`/`DVSS` terminals to the common
canonical ground network and its `VDD`/`DVDD` terminals to the local canonical
power segment. Each `brk5` instance connects its available `VSS` terminal but
does not reconnect the broken VDD/DVDD rails. Generated DEF and Verilog
component counts must match.

Generated filler instance names encode their physical gap and a one-based
suffix. The gap before the first pad on a side is index 00; the gap after pad
01 is index 01, and so on. Default fillers use `FILL_<side><gap>_<n>` and break
fillers use `BRK_<side><gap>_<n>`, for example `FILL_E00_1` and `BRK_E10_1`.
The same instance names appear in DEF and structural Verilog.

There are no default global-ground pads at E11, E12, W11, or W12. Each
break-delimited group derives its power and ground ownership from the single
participant power pad and single participant ground pad assigned within that
group. I/O and filler cells in the group connect their supply terminals to
those canonical power and ground pad nets. Every DVDD pad inherently breaks the
VDD/DVDD rails, so two DVDD pads cannot electrically belong to the same
segment. `BREAK ;` supplies the separately required `brk5` physical isolation;
it also resets the generator's power and ground counters, but is not the
mechanism that separates DVDD-pad power domains. A powered
segment uses the bare canonical name of its DVDD pad as its supply port and
net. Any I/O or filler region that has no DVDD source uses an explicitly
declared, unique floating net named `FLOAT_VDD_<n>`. Floating regions must not
reuse a physical pad name or silently connect to another power segment.

The pad CSV format is:

canonical_pin_name,project_pin_name,type,x,y

canonical_pin_name is the canonical slot such as W01. For an allocated
participant pad, project_pin_name is the original project pin name prefixed by
the team code, for example A01_RST. It is empty for pads not allocated to that
project. type is the I/O macro name. x and y are micron
coordinates at the center of the single square on the configured GDS marker
layer/datatype pair (37/0 by default for GF180), within that I/O macro after
applying the DEF instance placement and orientation. The CSV coordinates must be derived from the source I/O GDS
geometry, not from the LEF macro bounding box. Break, filler, and corner instances are
excluded. The configured marker layer/datatype must be searched recursively through the complete I/O
macro hierarchy. The search must virtually flatten and merge the recursive
geometry in macro-local coordinates before selecting the square and applying
the top-level DEF transform. DEF orientation transforms must include the
normalization translation implied by the macro's declared LEF SIZE; applying
only a rotation/reflection matrix is incorrect for every orientation except N.
Missing LEF SIZE data or ambiguous square marker geometry is a deterministic
error.

***Key rule:** Physical slot order must always be represented explicitly. Do not derive it from words such as clockwise, counter-clockwise, left-to-right, or package orientation when an ordered list can be provided instead.*

# **32\. GDS project audit**

The repository provides `chipathon2026-system/scripts/gds_project_audit.py` to
audit successfully downloaded project layouts. This section is the sole
authoritative specification for that audit; no separate audit specification is
maintained.

## **32.1 Inputs and project selection**

The audit reads the GDS download report and emits one CSV row for each project
record containing a successful `GDS saved:` entry. Summary and failed-download
records are skipped. From each successful record it obtains the team code,
local `info.yaml`, exact `TOP_LAYOUT`, and local GDS path. Relative paths are
resolved from the report working directory. The named `TOP_LAYOUT` must exist;
the first KLayout top cell is never an implicit substitute.

The saved GDS must be a usable binary, not a Git LFS pointer. The downloader
recognizes LFS pointers, retrieves the object through the Git LFS batch API,
applies returned download headers, and verifies both declared byte count and
SHA-256 before emitting `GDS saved:`. A valid existing binary may be reused,
but an existing pointer must be replaced. Missing files, unresolved pointers,
failed transfers, or identity mismatches are project download failures.

Each `info.yaml` pin requires a unique name and one canonical I/O type:
`power`, `ground`, `analog`, `input_cmos`, `input_schmitt`, `bidirectional`, or
`bidirectional_24ma`. Analog `secondary_esd` metadata is optional and does not
change audit categorization or counts.

## **32.2 Geometry measurements**

`width` and `height` are the dimensions in microns of exactly one rectangular
shape placed directly in `TOP_LAYOUT` on the configurable PR-boundary layer,
default `0/0`. Missing, multiple, nonrectangular, or hierarchical-only boundary
geometry is an error.

`computed_width` and `computed_height` are the transformed physical-content
bounding-box dimensions. Missing top-level PR_bndry on layer 0/datatype 0
is a nonfatal diagnostic: print a team-qualified error, leave PR-boundary
dimensions blank, and continue physical-content measurement and pin auditing.
This diagnostic alone does not fail `--strict`; independent content or pin
errors retain their normal failure behavior.

`computed_width` and `computed_height` are the transformed physical-content
bounding-box dimensions used for project-block selection. Standard-cell
membership is defined exclusively by exact, case-sensitive GDS cell-name
membership in the `MACRO` declarations of the configured standard-cell LEFs;
cell-name prefixes are never guessed. By default the configured LEFs are every
`*.lef` under
`<TECH_PDK>/libs.ref/gf180mcu_fd_sc_mcu7t5v0/lef`, replaceable by repeated
`--std-cell-lef` options.

A cell influences project type only when it contains direct non-text geometry
on a mapped poly, diffusion, or MIM-cap layer. Via cells and other cells with
only metal/cut geometry are ignored. Relevant cells are divided by exact LEF
macro membership: only standard cells means `Digital`, only non-standard cells
means `Analog`, and both means `Mixed signal`.

A standard-cell macro is excluded from the computed boundary when its LEF `CLASS` contains a whole word
`WELLTAP`, `TAP`, `SPACER`, `FILL`, `ENDCAP`, `ANTENNACELL`, `TIEHIGH`, or
`TIELOW`, case-insensitively. Because GF180 buffer and delay cells use only
`CLASS CORE`, also exclude exact GF180 macro families whose final name segment
matches `buf_<drive>`, `bufz_<drive>`, `clkbuf_<drive>`, or
`dly<variant>_<drive>`. These name-family exclusions apply only after the cell
has been established as a standard-cell macro by the configured LEF. A project
Computed geometry does not depend on this type classification. It is always
the union of: (1) transformed geometry on all GDS layers from matched macro
cells remaining after these exclusions, including recognized standard cells
that contain no semantic device-layer geometry; and (2) transformed geometry
on the semantic poly, diffusion, and MIM-cap layers from non-standard cells.
Cells whose names contain
`secondary_esd`, case-insensitively, and their complete hierarchy are excluded.
There is no hard-coded semantic-layer fallback. Unreadable LEFs or layer
properties, missing semantic mappings, or no positive-area result leave the
computed dimensions empty and add a deterministic audit error without
discarding valid PR-boundary dimensions.

For GF180, the layer-properties semantic mapping recognizes `Poly2` as poly,
`COMP` as diffusion, and `FuseTop` as MIM-cap geometry. In the GF180 `.lyp`
these labels may appear as the prefix of `<source>` values such as
`Poly2 30/0@1`, `COMP 22/0@1`, and `FuseTop 75/0@1` while `<name>` is empty;
the parser must accept both that representation and the conventional form with
the semantic label in `<name>` and only `layer/datatype@view` in `<source>`.

## **32.3 Ordered power/ground grouping**

The ordered `info.yaml` pin list is partitioned into groups. The first pin
starts the first group. A second `power` encountered in a group starts a new
group at that power pin; independently, a second `ground` starts a new group at
that ground pin. Signal pins remain in the current group. Every completed and
final group must contain exactly one power and exactly one ground. Incomplete
groups add an audit error but do not prevent the remaining text and geometry
checks.

## **32.4 Top-level text and interface matching**

Only text placed directly in `TOP_LAYOUT` participates. Unique spelling and
occurrence count are retained. Matching is case-insensitive while the actual
layout spelling remains diagnostic. Text containing `.` is ignored before
unmatched processing.

Power, ground, and analog each require exactly one direct text name equal to
the configured pin name. Input CMOS and Schmitt pads require the configured
base name as their data port. Bidirectional pads recognize names derived by
inserting `_IN` and `_OUT` before any trailing bus index; at least one of those
two data names is required and both are accounted for when present.

Optional digital control names are recognized but never required. A control
suffix is inserted before a trailing `[<decimal-index>]`. Recognized controls
include the terminals applicable to the selected GF180 I/O macro, including
`IE`, `OE`, `PU`, `PD`, `PDRV0`, `PDRV1`, `SL`, and `CS`. Scalar and indexed
forms are accepted case-insensitively. An index after the suffix, a nondecimal
index, an inapplicable terminal, or a name belonging to another configured pad
is not recognized.

A configured one-to-one pin missing its required text, or a digital pad failing
its required data-port check, is prefixed with `*` in its compact category
field and produces a detailed error. Optional missing controls do not produce
errors. `unmatched` contains each remaining unique direct top-level text once
after recognized data and optional controls have been removed.

## **32.5 CSV and failures**

The exact CSV columns are:

```text
Team,Top_cell,gds_file,width,height,computed_width,computed_height,type,ws_def_ready,gf_def_ready,drc_ready,lvs_ready,power,ground,analog,input_cmos,input_schmitt,bidirectional,bidirectional_24ma,unmatched,errors
```

`type` is exactly `Analog`, `Digital`, or `Mixed signal` according to the
device-layer classification above. If no device-layer geometry determines a
type, use `?`; this does not clear valid computed dimensions obtained by the
type-independent measurement rule. Record other classification failures in
`errors`.

The four readiness fields are read from `project.ws_def_ready`,
`project.gf_def_ready`, `project.drc_ready`, and `project.lvs_ready` in
`info.yaml`. Emit `Y` only for a YAML Boolean `true`, `N` only for a YAML
Boolean `false`, and `?` when `project` is missing or not a mapping, when the
key is missing, or when its value has any other YAML type or spelling.

`gds_file` displays the filename while the full path remains internal. Every
pad-type and unmatched field has `<count>:<space-separated-names>` form and is
`0:` when empty. Repeated layout text counts once for matching/unmatched but
retains its occurrence count for diagnostics. The audit prints overall totals
for every pad type.

Per-project problems are accumulated in `errors` and do not prevent other
successfully downloaded projects from being audited. These include missing
inputs, malformed or duplicate pins, unknown types, invalid grouping, missing
top cell or PR boundary, interface mismatches, and computed-size failures.
`--strict` returns nonzero if any successfully downloaded project has an audit
error.
