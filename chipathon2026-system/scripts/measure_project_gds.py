"""KLayout batch script: measure the physical project content bounding box."""

import json
import re
import shlex
import xml.etree.ElementTree as ET

import pya


def required_variable(name):
    value = globals().get(name)
    if value is None or str(value).strip() == "":
        raise RuntimeError(f"missing required -rd {name}=... value")
    return str(value)


def semantic_layers(path):
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise RuntimeError(f"cannot read PDK layer properties {path}: {exc}") from exc

    found = {"poly": set(), "diffusion": set(), "mim_cap": set()}
    patterns = {
        "poly": re.compile(r"\bpoly(?:2)?\b", re.IGNORECASE),
        "diffusion": re.compile(r"\b(?:diff(?:usion)?|comp|active)\b", re.IGNORECASE),
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
            f"PDK layer properties {path} do not map required semantic layer(s): "
            + ", ".join(missing)
        )
    return found


def standard_cell_macros(paths):
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
            raise RuntimeError(f"cannot read standard-cell LEF {path}: {exc}") from exc
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
                        raise RuntimeError(f"duplicate standard-cell LEF macro {macro_name}")
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
        raise RuntimeError(f"unterminated standard-cell LEF macro {macro_name}")
    if not macros:
        raise RuntimeError("no standard-cell macros were found in the configured LEFs")
    return macros


def physical_shape(shape):
    return not shape.is_text()


def merged_bbox(boxes):
    boxes = [box for box in boxes if not box.empty()]
    if not boxes:
        raise RuntimeError("selected project geometry has no positive-area bounding box")
    return pya.Box(
        min(box.left for box in boxes),
        min(box.bottom for box in boxes),
        max(box.right for box in boxes),
        max(box.top for box in boxes),
    )


def transformed_shape_bbox(iterator):
    return iterator.shape().bbox().transformed(iterator.trans())


def main():
    input_manifest = globals().get("input_manifest")
    if input_manifest is not None and str(input_manifest).strip():
        with open(str(input_manifest), "r", encoding="utf-8") as stream:
            input_path = str(json.load(stream)["project_gds"])
    else:
        input_path = required_variable("input_gds")
    output_path = required_variable("output_json")
    outline_layer = int(globals().get("outline_layer", 0))
    outline_datatype = int(globals().get("outline_datatype", 0))
    layer_properties = required_variable("layer_properties")
    lef_paths = shlex.split(required_variable("std_cell_lefs"))

    layout = pya.Layout()
    layout.read(input_path)
    top_cells = list(layout.top_cells())
    if len(top_cells) != 1:
        raise RuntimeError(
            f"project GDS must contain exactly one top cell; found {[cell.name for cell in top_cells]}"
        )
    top = top_cells[0]
    macros = standard_cell_macros(lef_paths)
    mapped_layers = semantic_layers(layer_properties)
    outline_index = layout.find_layer(outline_layer, outline_datatype)
    layer_indexes = {
        name: {
            index
            for layer, datatype in layers
            if (index := layout.find_layer(layer, datatype)) is not None and index >= 0
        }
        for name, layers in mapped_layers.items()
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

    physical_layers = set().union(*layer_indexes.values())
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
            physical_shape(shape)
            for layer_index in physical_layers
            for shape in layout.cell(cell_index).shapes(layer_index).each()
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
        iterator = pya.RecursiveShapeIterator(layout, top, list(layout.layer_indexes()))
        iterator.unselect_all_cells()
        iterator.select_cells(included_std_cells)
        while not iterator.at_end():
            if physical_shape(iterator.shape()):
                boxes.append(transformed_shape_bbox(iterator))
            iterator.next()
    if analog_cell_indexes:
        iterator = pya.RecursiveShapeIterator(layout, top, sorted(physical_layers))
        iterator.unselect_all_cells()
        iterator.select_cells(list(analog_cell_indexes))
        while not iterator.at_end():
            if physical_shape(iterator.shape()) and iterator.layer() in physical_layers:
                boxes.append(transformed_shape_bbox(iterator))
            iterator.next()
    bbox = merged_bbox(boxes)
    if bbox.width() <= 0 or bbox.height() <= 0:
        raise RuntimeError("project physical-content bounding box must have positive width and height")

    outline_rectangles = []
    outline_other = 0
    if outline_index is not None and outline_index >= 0:
        for shape in top.shapes(outline_index).each():
            if shape.is_box():
                outline_rectangles.append(shape.box)
            elif shape.is_polygon() and shape.polygon.is_box():
                outline_rectangles.append(shape.polygon.bbox())
            else:
                outline_other += 1
    outline = None
    if len(outline_rectangles) != 1 or outline_other:
        print(
            f"top cell {top.name!r} layer {outline_layer}/{outline_datatype} must contain "
            f"exactly one PR-boundary rectangle and no other shapes; found "
            f"{len(outline_rectangles)} rectangles and {outline_other} other shapes"
        )
    else:
        candidate = outline_rectangles[0]
        if candidate.width() > 0 and candidate.height() > 0:
            outline = candidate
        else:
            print("project PR-boundary rectangle has nonpositive width or height; continuing")

    dbu = layout.dbu
    result = {
        "source_gds": input_path,
        "top_cell": top.name,
        "block_type": block_type,
        "layout_dbu_microns": dbu,
        "physical_bbox_dbu": [bbox.left, bbox.bottom, bbox.right, bbox.top],
        "physical_bbox_microns": [
            format(bbox.left * dbu, ".12g"), format(bbox.bottom * dbu, ".12g"),
            format(bbox.right * dbu, ".12g"), format(bbox.top * dbu, ".12g"),
        ],
        "prboundary_dbu": [
            outline.left, outline.bottom, outline.right, outline.top,
        ] if outline is not None else None,
        "prboundary_microns": [
            format(outline.left * dbu, ".12g"), format(outline.bottom * dbu, ".12g"),
            format(outline.right * dbu, ".12g"), format(outline.top * dbu, ".12g"),
        ] if outline is not None else None,
        "width_microns": format(bbox.width() * dbu, ".12g"),
        "height_microns": format(bbox.height() * dbu, ".12g"),
        "top_cell_text": [],
    }
    for layer_index in layout.layer_indexes():
        layer_info = layout.get_info(layer_index)
        for shape in top.shapes(layer_index).each():
            if shape.is_text():
                result["top_cell_text"].append({
                    "text": shape.text.string,
                    "layer": layer_info.layer,
                    "datatype": layer_info.datatype,
                })
    with open(output_path, "w", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")

    print(
        f"Measured {top.name} {block_type} physical content: "
        f"{result['width_microns']} x {result['height_microns']} um"
    )


main()
