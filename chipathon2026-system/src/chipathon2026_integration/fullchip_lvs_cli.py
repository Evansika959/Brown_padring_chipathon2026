from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import requests
import yaml

from .errors import ConfigError, IntegrationError
from .fullchip import file_sha256, json_dump, load_chip_request
from .fullchip_lvs import (
    download_projects,
    ensure_lvs_root,
    render_environment,
    run_lvs,
)


def _format_elapsed(seconds: float) -> str:
    total_ms = max(0, round(seconds * 1000))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{milliseconds:03d}"


def _print_download_summary(manifest: dict, data_root: Path, elapsed: float) -> None:
    projects = manifest.get("projects", [])
    failures = manifest.get("failures", [])
    print(f"Downloaded {len(projects)} projects to {data_root}; failed: {len(failures)}")
    if failures:
        print("Failed projects:", file=sys.stderr)
        for failure in failures:
            print(
                f"  {failure.get('team', '<unknown>')}: "
                f"{failure.get('error', 'unknown error')}",
                file=sys.stderr,
            )
    print(f"Total download wall-clock time: {_format_elapsed(elapsed)}")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run integrated Chipathon device-level LVS")
    p.add_argument("action", choices=("run",))
    p.add_argument("integration_yaml", type=Path, nargs="?")
    p.add_argument("--base-chip", type=Path)
    p.add_argument("--data-root", type=Path, default=Path("data"))
    p.add_argument("--integrated-root", type=Path, default=Path("build/integrated"))
    p.add_argument("--output-root", type=Path, default=Path("build/integrated/lvs"))
    p.add_argument("--lvs-root", type=Path, default=Path("build/tools/extra_be_checks"))
    p.add_argument("--pdk-root", type=Path, default=Path("/foss/pdks"))
    p.add_argument("--pdk", default="gf180mcuD")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.action != "download" and args.integration_yaml is None:
            raise ConfigError(f"integration_yaml is required for LVS action {args.action}")
        chip = (
            load_chip_request(args.integration_yaml, base_chip_override=args.base_chip)
            if args.integration_yaml is not None else None
        )
        args.output_root.mkdir(parents=True, exist_ok=True)
        if args.action == "run":
            ensure_lvs_root(args.lvs_root)
        if args.action == "run":
            config = args.output_root / f"{chip.name}_lvs_config.json"
            if not config.is_file():
                raise ConfigError(f"generated LVS configuration not found: {config}")
            manifest_path = args.output_root / f"{chip.name}_lvs_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            environment = manifest.get("environment")
            if not isinstance(environment, dict) or any(
                not isinstance(key, str) or not isinstance(value, str)
                for key, value in environment.items()
            ):
                raise ConfigError(f"generated LVS environment not found in {manifest_path}")
            environment_path = args.output_root / f"{chip.name}_lvs.env"
            if environment_path.read_text(encoding="utf-8") != render_environment(environment):
                raise ConfigError(f"generated LVS environment file disagrees with {manifest_path}")
            hashes = manifest.get("hashes")
            if not isinstance(hashes, dict):
                raise ConfigError(f"generated LVS input hashes not found in {manifest_path}")
            for raw_path, expected_hash in hashes.items():
                path = Path(raw_path)
                if not path.is_file():
                    raise ConfigError(f"integration-time LVS input is missing: {path}")
                actual_hash = file_sha256(path)
                if actual_hash != expected_hash:
                    raise ConfigError(
                        f"integration-time LVS input changed: {path}; "
                        f"expected {expected_hash}, found {actual_hash}"
                    )
            status = run_lvs(
                config, args.lvs_root, args.output_root / "work",
                args.output_root / "logs", args.output_root / "signoff",
                args.pdk_root, args.pdk, environment=environment,
            )
            summary = {"schema_version": 1, "chip_name": chip.name,
                       "status": "pass" if status == 0 else "fail", "returncode": status}
            json_dump(args.output_root / f"{chip.name}_lvs_summary.json", summary)
            (args.output_root / f"{chip.name}_lvs_summary.txt").write_text(
                f"Chip: {chip.name}\nDevice-level LVS: {summary['status'].upper()}\nReturn code: {status}\n",
                encoding="utf-8",
            )
            if status:
                return status
        print(f"LVS {args.action} completed for {chip.name}")
        return 0
    except (IntegrationError, OSError, ValueError, json.JSONDecodeError, yaml.YAMLError, requests.RequestException,
            __import__("subprocess").CalledProcessError) as exc:
        try:
            args.output_root.mkdir(parents=True, exist_ok=True)
            (args.output_root / "lvs_failure.txt").write_text(
                f"Status: INVALID\nAction: {args.action}\nError: {exc}\n", encoding="utf-8"
            )
        except OSError:
            pass
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


def download_main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Download all Chipathon project data from repo.list")
    p.add_argument("repo_list", type=Path)
    p.add_argument("--data-root", type=Path, default=Path("data"))
    p.add_argument("--yes", action="store_true")
    args = p.parse_args(sys.argv[1:] if argv is None else argv)
    started = None

    def mark_download_started() -> None:
        nonlocal started
        started = time.perf_counter()

    try:
        manifest = download_projects(
            args.repo_list, args.data_root, assume_yes=args.yes,
            start_callback=mark_download_started,
        )
        json_dump(args.data_root / "download_manifest.json", manifest)
        _print_download_summary(manifest, args.data_root, time.perf_counter() - started)
        return 1 if manifest["failures"] else 0
    except (IntegrationError, OSError, ValueError, requests.RequestException,
            __import__("subprocess").CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        if started is not None:
            print(
                f"Total download wall-clock time: "
                f"{_format_elapsed(time.perf_counter() - started)}"
            )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
