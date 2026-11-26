from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .errors import ConfigError


@dataclass(frozen=True)
class BlockVariant:
    code: str
    slots: tuple[str, ...]
    origin: tuple[Decimal, Decimal]
    width: Decimal
    height: Decimal
    area: int
    blockages: tuple[tuple[Decimal, Decimal, Decimal, Decimal], ...] = ()
    metal2_blockages: tuple[tuple[Decimal, Decimal, Decimal, Decimal], ...] = ()


def _slots(*ranges: str) -> tuple[str, ...]:
    result: list[str] = []
    for value in ranges:
        side = value[0]
        start, end = (int(part[1:]) for part in value.split("-"))
        step = 1 if end >= start else -1
        result.extend(f"{side}{number:02d}" for number in range(start, end + step, step))
    return tuple(result)


def load_block_variants(path: Path) -> dict[str, BlockVariant]:
    """Load project geometry and ordered pad ranges from the block TSV."""
    variants: dict[str, BlockVariant] = {}
    try:
        with path.open(encoding="utf-8", newline="") as source:
            reader = csv.DictReader(source, delimiter="\t")
            required = {"Blocks", "Pin order", "Pin count", "Placement", "width", "height", "Area", "block"}
            if not required.issubset(reader.fieldnames or []):
                raise ValueError("missing required columns")
            for row in reader:
                code = row["Blocks"].strip()
                if not code or code in variants:
                    raise ValueError(f"empty or duplicate block {code!r}")
                origin = tuple(Decimal(v.strip()) for v in row["Placement"].split(","))
                width, height = Decimal(row["width"]), Decimal(row["height"])
                area = int(row["Area"].replace(",", ""))
                if len(origin) != 2 or not all(v.is_finite() and v >= 0 for v in origin):
                    raise ValueError(f"invalid placement for {code}")
                if not all(v.is_finite() and v > 0 for v in (width, height)) or area <= 0:
                    raise ValueError(f"invalid size or area for {code}")
                blockages = []
                for rectangle in row["block"].split("&"):
                    if not rectangle.strip():
                        continue
                    lower, upper = rectangle.strip().split("-")
                    coords = tuple(Decimal(v.strip()) for v in (lower + "," + upper).split(","))
                    if (len(coords) != 4 or not all(v.is_finite() for v in coords)
                            or not (0 <= coords[0] < coords[2] <= width
                                    and 0 <= coords[1] < coords[3] <= height)):
                        raise ValueError(f"invalid blockage for {code}")
                    blockages.append(coords)
                slots = _slots(*(v.strip() for v in row["Pin order"].split(",")))
                pin_count = int(row["Pin count"])
                if pin_count != len(slots):
                    raise ValueError(
                        f"{code}: Pin count {pin_count} does not match "
                        f"{len(slots)} slots in Pin order"
                    )
                if len(set(slots)) != len(slots):
                    raise ValueError(f"{code}: duplicate slots in Pin order")
                variants[code] = BlockVariant(code, slots, origin, width, height, area, tuple(blockages))
        if not variants:
            raise ValueError("no blocks")
    except (OSError, ValueError, InvalidOperation, KeyError, TypeError) as exc:
        raise ConfigError(f"invalid block table {path}: {exc}") from exc
    return variants


BLOCK_DATA_PATH = Path(__file__).with_name("blocks.data")
BLOCK_VARIANTS = load_block_variants(BLOCK_DATA_PATH)

# Variants made for one chip floorplan (chip 13: CV8 / A20T / A21S). They are used only
# when an integration request names them; select_block_variants never picks them, so
# adding them does not change the automatic choice for any other project.
MANUAL_ONLY_VARIANTS = frozenset({"CV8", "A20T", "A21S"})
