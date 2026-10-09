# KLayout Python helper for gds_project_audit.py
#
# Invoked as:
#   klayout -b -r klayout_gds_inspect.py \
#       -rd gds_file=... -rd top_cell=... -rd output_json=...
#
# Uses only Python's standard library plus KLayout's built-in pya module.

import json
import re
import traceback
import xml.etree.ElementTree as ET

import pya


def _rd_value(name, required=True, default=None):
    value = globals().get(name)
    if value is None and required:
        raise RuntimeError("missing -rd %s=..." % name)
    return str(default if value is None else value)


def _find_layer(layout, layer, datatype):
    try:
        return layout.find_layer(layer, datatype)
    except TypeError:
        return layout.find_layer(pya.LayerInfo(layer, datatype))


def _cell_by_name(layout, name):
    cell = layout.cell(name)
    if cell is None:
        raise RuntimeError("TOP_LAYOUT cell %r not found in GDS" % name)
    return cell


def _shape_is_text(shape):
    value = getattr(shape, "is_text")
    return value() if callable(value) else bool(value)


def _shape_text_string(shape):
    # KLayout versions expose text either as text_string or text.string.
    if hasattr(shape, "text_string"):
        value = getattr(shape, "text_string")
        return str(value() if callable(value) else value)
    text = shape.text
    value = getattr(text, "string")
    return str(value() if callable(value) else value)


def _semantic_layers(path):
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise RuntimeError("cannot read PDK layer properties %s: %s" % (path, exc))

    found = {"poly": set(), "diffusion": set(), "mim_cap": set()}
    patterns = {
        "poly": re.compile(r"\bpoly(?:2)?\b", re.IGNORECASE),
        "diffusion": re.compile(
            r"\b(?:diff(?:usion)?|comp|active)\b", re.IGNORECASE
        ),
        "mim_cap": re.compile(
            r"\b(?:mim(?:[_ ]?cap|\d+)?|capm|cap[_ ]mim|fusetop)\b",
            re.IGNORECASE,
        ),
    }
    for entry in root.findall(".//properties"):
        name = entry.findtext("name", "")
        source = entry.findtext("source", "")
        match = re.match(
            r"\s*(?:(.*?)\s+)?(\d+)\s*/\s*(\d+)(?:@\d+)?\s*$", source
        )
        if match is None:
            continue
        semantic_name = name.strip() or (match.group(1) or "").strip()
        for semantic, pattern in patterns.items():
            if pattern.search(semantic_name):
                found[semantic].add((int(match.group(2)), int(match.group(3))))

    missing = [name for name, layers in found.items() if not layers]
    if missing:
        raise RuntimeError(
            "PDK layer properties %s do not map semantic layer(s): %s"
            % (path, ", ".join(missing))
        )
    return found


def _standard_cell_macros(paths):
    macros = {}
    macro_name = None
    macro_class = ""
    macro_re = re.compile(r"^\s*MACRO\s+(\S+)")
    class_re = re.compile(r"^\s*CLASS\s+(.+?)\s*;")
    end_re = re.compile(r"^\s*END\s+(\S+)")
    for path in paths:
        try:
            stream = open(path, "r", encoding="utf-8")
        except OSError as exc:
            raise RuntimeError("cannot read standard-cell LEF %s: %s" % (path, exc))
        with stream:
            for line in stream:
                if macro_name is None:
                    match = macro_re.match(line)
                    if match:
                        macro_name = match.group(1)
                        macro_class = ""
                    continue
                if match := class_re.match(line):
                    macro_class = match.group(1).upper()
                    continue
                if (match := end_re.match(line)) and match.group(1) == macro_name:
                    if macro_name in macros:
                        raise RuntimeError(
                            "duplicate standard-cell LEF macro %s" % macro_name
                        )
                    excluded_class = bool(re.search(
                        r"\b(?:WELLTAP|TAP|SPACER|FILL|ENDCAP|ANTENNACELL|"
                        r"TIEHIGH|TIELOW)\b", macro_class
                    ))
                    excluded_name = bool(re.search(
                        r"(?:^|__)(?:bufz?|clkbuf|dly[a-z]*)_\d+$",
                        macro_name,
                        re.IGNORECASE,
                    ))
                    macros[macro_name] = excluded_class or excluded_name
                    macro_name = None
                    macro_class = ""
    if macro_name is not None:
        raise RuntimeError("unterminated standard-cell LEF macro %s" % macro_name)
    if not macros:
        raise RuntimeError("no standard-cell macros found in configured LEFs")
    return macros


def _is_physical_shape(shape):
    return not _shape_is_text(shape)


def _transformed_bbox(iterator):
    return iterator.shape().bbox().transformed(iterator.trans())


def _content_bbox(layout, top, layer_properties, std_cell_lefs):
    macros = _standard_cell_macros(std_cell_lefs)
    semantic = _semantic_layers(layer_properties)
    layer_indexes = {
        index
        for pairs in semantic.values()
        for layer, datatype in pairs
        for index in [_find_layer(layout, layer, datatype)]
        if index is not None and int(index) >= 0
    }

    reachable = {top.cell_index()}
    pending = [top]
    while pending:
        cell = pending.pop()
        for instance in cell.each_inst():
            child = layout.cell(instance.cell_index)
            if child.cell_index() not in reachable:
                reachable.add(child.cell_index())
                pending.append(child)

    excluded_secondary_esd = set()
    pending = [
        cell for cell in layout.each_cell()
        if "secondary_esd" in cell.name.casefold()
    ]
    while pending:
        cell = pending.pop()
        if cell.cell_index() in excluded_secondary_esd:
            continue
        excluded_secondary_esd.add(cell.cell_index())
        pending.extend(layout.cell(inst.cell_index) for inst in cell.each_inst())

    all_std_cell_indexes = {
        cell_index for cell_index in reachable
        if layout.cell(cell_index).name in macros
    }
    relevant_cells = {
        cell_index
        for cell_index in reachable - excluded_secondary_esd
        if any(
            _is_physical_shape(shape)
            for layer in layer_indexes
            for shape in layout.cell(cell_index).shapes(layer).each()
        )
    }
    std_device_indexes = relevant_cells & all_std_cell_indexes
    analog_cell_indexes = relevant_cells - all_std_cell_indexes
    if std_device_indexes and analog_cell_indexes:
        block_type = "mixed_signal"
    elif std_device_indexes:
        block_type = "digital"
    elif analog_cell_indexes:
        block_type = "analog"
    else:
        block_type = "unknown"

    boxes = []
    included_std_cells = [
        cell_index for cell_index in all_std_cell_indexes
        if not macros[layout.cell(cell_index).name]
    ]
    if included_std_cells:
        iterator = pya.RecursiveShapeIterator(
            layout, top, list(layout.layer_indexes())
        )
        iterator.unselect_all_cells()
        iterator.select_cells(included_std_cells)
        while not iterator.at_end():
            if _is_physical_shape(iterator.shape()):
                boxes.append(_transformed_bbox(iterator))
            iterator.next()
    if analog_cell_indexes:
        iterator = pya.RecursiveShapeIterator(layout, top, sorted(layer_indexes))
        iterator.unselect_all_cells()
        iterator.select_cells(list(analog_cell_indexes))
        while not iterator.at_end():
            if _is_physical_shape(iterator.shape()) and iterator.layer() in layer_indexes:
                boxes.append(_transformed_bbox(iterator))
            iterator.next()
    boxes = [box for box in boxes if not box.empty()]
    if not boxes:
        raise RuntimeError("selected project geometry has no positive-area bounding box")
    bbox = pya.Box(
        min(box.left for box in boxes),
        min(box.bottom for box in boxes),
        max(box.right for box in boxes),
        max(box.top for box in boxes),
    )
    if bbox.width() <= 0 or bbox.height() <= 0:
        raise RuntimeError("computed physical-content bounding box has no positive area")
    dbu = float(layout.dbu)
    return block_type, bbox, dbu


def inspect(gds_path, top_name, layer_properties, std_cell_lefs):
    layout = pya.Layout()
    layout.read(gds_path)

    cell = _cell_by_name(layout, top_name)

    layer_index = _find_layer(layout, 0, 0)
    if layer_index is None or int(layer_index) < 0:
        raise RuntimeError(
            "no layer 0/datatype 0 exists in GDS for TOP_LAYOUT %r" % top_name
        )

    # Include only shapes directly in TOP_LAYOUT; instantiated child geometry
    # is intentionally excluded from the PR-boundary measurement.
    bbox = None
    for shape in cell.shapes(layer_index).each():
        shape_bbox = shape.bbox()
        bbox = shape_bbox if bbox is None else bbox + shape_bbox

    if bbox is None or bbox.empty():
        raise RuntimeError(
            "TOP_LAYOUT %r has no top-level shapes on layer 0/datatype 0"
            % top_name
        )

    dbu = float(layout.dbu)
    left = bbox.left * dbu
    bottom = bbox.bottom * dbu
    right = bbox.right * dbu
    top = bbox.top * dbu

    counts = {}
    for layer in layout.layer_indexes():
        for shape in cell.shapes(layer).each():
            if _shape_is_text(shape):
                text = _shape_text_string(shape)
                # Text containing '.' is intentionally ignored by the audit.
                if "." in text:
                    continue
                counts[text] = counts.get(text, 0) + 1

    text_counts = [
        {"text": text, "count": counts[text]}
        for text in sorted(counts)
    ]

    result = {
        "ok": True,
        "top_cell": top_name,
        "dbu_um": dbu,
        "boundary_bbox_um": [left, bottom, right, top],
        "width_um": right - left,
        "height_um": top - bottom,
        "text_counts": text_counts,
    }
    try:
        block_type, content_bbox, _ = _content_bbox(
            layout, cell, layer_properties, std_cell_lefs
        )
        result.update({
            "block_type": block_type,
            "computed_bbox_um": [
                content_bbox.left * dbu,
                content_bbox.bottom * dbu,
                content_bbox.right * dbu,
                content_bbox.top * dbu,
            ],
            "computed_width_um": content_bbox.width() * dbu,
            "computed_height_um": content_bbox.height() * dbu,
        })
    except Exception as exc:
        result.update({
            "computed_width_um": None,
            "computed_height_um": None,
            "computed_error": str(exc),
        })
    return result


def main():
    output_path = None
    try:
        gds_path = _rd_value("gds_file")
        top_name = _rd_value("top_cell")
        output_path = _rd_value("output_json")
        layer_properties = _rd_value("layer_properties")
        std_cell_lefs = json.loads(_rd_value("std_cell_lefs_json"))
        result = inspect(gds_path, top_name, layer_properties, std_cell_lefs)
    except Exception as exc:
        result = {
            "ok": False,
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }

    if output_path is None:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
        f.write("\n")

    return 0 if result.get("ok") else 1


raise SystemExit(main())
