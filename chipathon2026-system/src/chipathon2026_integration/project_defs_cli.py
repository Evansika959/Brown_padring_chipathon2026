from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from .errors import IntegrationError
from .fullchip import load_chip_request


SYSTEM_ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_ROOT = SYSTEM_ROOT.parent


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Generate canonical project DEFs for every project in an integration YAML"
    )
    p.add_argument("integration_yaml", type=Path)
    p.add_argument("--base-chip", type=Path)
    p.add_argument("--data-root", type=Path, default=Path("data"))
    p.add_argument("--output-dir", type=Path, default=Path("build/padframes"))
    p.add_argument("--template", type=Path, default=SYSTEM_ROOT / "padring_template.cfg")
    p.add_argument("--padring", type=Path, default=REPOSITORY_ROOT / "build/padring")
    p.add_argument("--pdk-root", type=Path, default=Path("/foss/pdks"))
    p.add_argument("--pdk", default="gf180mcuD")
    p.add_argument("--tech-pdk", type=Path)
    p.add_argument("--klayout", default="klayout")
    p.add_argument("--def-dbu", type=float, default=0.005)
    p.add_argument("--outline-layer", type=int, default=0)
    p.add_argument("--outline-datatype", type=int, default=0)
    p.add_argument("--make", default="make", dest="make_program")
    return p


def build_command(args: argparse.Namespace, teams: list[str]) -> list[str]:
    tech_pdk = args.tech_pdk or args.pdk_root / args.pdk
    return [
        args.make_program,
        "-f", str(SYSTEM_ROOT / "Makefile.padframe"),
        *[f"project-def-{team}" for team in teams],
        f"DATA_ROOT={args.data_root}",
        f"OUTPUT_DIR={args.output_dir}",
        f"TEMPLATE={args.template}",
        f"PADRING={args.padring}",
        f"PDK_ROOT={args.pdk_root}",
        f"PDK={args.pdk}",
        f"TECH_PDK={tech_pdk}",
        f"KLAYOUT={args.klayout}",
        f"DEF_DBU={args.def_dbu}",
        f"PROJECT_OUTLINE_LAYER={args.outline_layer}",
        f"PROJECT_OUTLINE_DATATYPE={args.outline_datatype}",
    ]


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        chip = load_chip_request(args.integration_yaml, base_chip_override=args.base_chip)
        teams = list(dict.fromkeys(project.team for project in chip.projects))
        if not teams:
            raise IntegrationError("integration YAML contains no projects")
        print(f"Generating project DEFs for {len(teams)} project(s): {', '.join(teams)}")
        subprocess.run(build_command(args, teams), text=True, check=True)
        print(f"Generated project DEFs under {args.output_dir}")
        return 0
    except (IntegrationError, OSError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
