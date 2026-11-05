from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import shlex
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable
from urllib.parse import quote

import requests
import yaml

from .errors import ConfigError
from .fullchip import file_sha256, json_dump, load_chip_request
from .lvs import load_lvs_config, resolve_json_variables, resolve_project_data


LVS_REPOSITORY = "https://github.com/d-m-bailey/extra_be_checks.git"
LVS_BRANCH = "wafer.space-run1"
RAW_ROOT = "https://raw.githubusercontent.com"
LFS_BATCH_ROOT = "https://github.com"
LFS_VERSION = "https://git-lfs.github.com/spec/v1"
DEPRECATED_SOURCE_KEY = "LVS_SPICE_FILES_TO_FIX"
SOURCE_KEYS = ("LVS_SPICE_FILES", "LVS_VERILOG_FILES")
LIST_KEYS = (
    "EXTRACT_FLATGLOB", "EXTRACT_ABSTRACT", "EXTRACT_CREATE_SUBCUT",
    "LVS_FLATTEN", "LVS_NOFLATTEN", "LVS_IGNORE", *SOURCE_KEYS,
)
VARIABLE_RE = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")


@dataclass(frozen=True)
class Repository:
    team: str
    name: str


def _as_list(value: Any, key: str) -> list[str]:
    if value is None:
        return []
    values = [value] if isinstance(value, str) else value
    if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
        raise ConfigError(f"{key} must be a string or list of strings")
    return [item.strip() for item in values if item.strip()]


def _reject_deprecated_sources(config: dict[str, Any]) -> None:
    if _as_list(config.get(DEPRECATED_SOURCE_KEY), DEPRECATED_SOURCE_KEY):
        raise ConfigError(
            f"{DEPRECATED_SOURCE_KEY} is deprecated and must be empty"
        )


def load_repo_list(path: Path) -> dict[str, Repository]:
    result: dict[str, Repository] = {}
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        if len(fields) != 2:
            raise ConfigError(f"{path}:{lineno}: expected '<team> <owner/repository>'")
        team, repo = fields
        key = team.casefold()
        if key in result:
            raise ConfigError(f"{path}:{lineno}: duplicate team mapping for {team}")
        if repo == "???" or repo.count("/") != 1:
            raise ConfigError(f"{path}:{lineno}: invalid repository for {team}: {repo}")
        result[key] = Repository(team, repo)
    return result


def _raw_url(repo: str, ref: str, path: str) -> str:
    encoded_path = "/".join(quote(part, safe="") for part in path.split("/"))
    return f"{RAW_ROOT}/{repo}/{quote(ref, safe='')}/{encoded_path}"


def _safe_repo_relative(value: str, context: str) -> str:
    value = value.strip()
    for prefix in ("$UPRJ_ROOT/", "${UPRJ_ROOT}/"):
        if value.startswith(prefix):
            value = value.removeprefix(prefix)
            break
    while value.startswith("./"):
        value = value[2:]
    path = PurePosixPath(value)
    if (not value or value in {"$UPRJ_ROOT", "${UPRJ_ROOT}"}
            or VARIABLE_RE.search(value) or path.is_absolute()
            or ".." in path.parts):
        raise ConfigError(f"unsafe repository-relative {context}: {value!r}")
    return path.as_posix()


def _lfs_pointer(data: bytes) -> tuple[str, int] | None:
    try:
        lines = data.decode("ascii").splitlines()
    except UnicodeDecodeError:
        return None
    if not lines or lines[0].strip() != f"version {LFS_VERSION}":
        return None
    oid = size = None
    for line in lines[1:]:
        if line.startswith("oid sha256:"):
            oid = line.removeprefix("oid sha256:").strip().lower()
        elif line.startswith("size "):
            raw_size = line.removeprefix("size ").strip()
            size = int(raw_size) if raw_size.isdigit() else None
    if oid is None or not re.fullmatch(r"[0-9a-f]{64}", oid) or size is None:
        raise ConfigError("invalid Git LFS pointer")
    return oid, size


def _download_lfs(session: requests.Session, repo: str, ref: str, oid: str, size: int) -> bytes:
    response = session.post(
        f"{LFS_BATCH_ROOT}/{repo}.git/info/lfs/objects/batch",
        headers={"Accept": "application/vnd.git-lfs+json", "Content-Type": "application/vnd.git-lfs+json"},
        json={"operation": "download", "transfers": ["basic"], "ref": {"name": f"refs/heads/{ref}"},
              "objects": [{"oid": oid, "size": size}], "hash_algo": "sha256"},
        timeout=60,
    )
    response.raise_for_status()
    objects = response.json().get("objects", [])
    obj = next((item for item in objects if item.get("oid") == oid), None)
    if obj is None or "error" in obj:
        raise ConfigError(f"Git LFS did not provide object {oid}")
    action = obj.get("actions", {}).get("download", {})
    if not action.get("href"):
        raise ConfigError(f"Git LFS did not provide a download URL for {oid}")
    download = requests.get(action["href"], headers=action.get("header", {}), timeout=(30, 300))
    download.raise_for_status()
    data = download.content
    if len(data) != size or hashlib.sha256(data).hexdigest() != oid:
        raise ConfigError(f"Git LFS verification failed for {oid}")
    return data


def get_repo_file(session: requests.Session, repo: str, ref: str, path: str) -> bytes:
    response = session.get(_raw_url(repo, ref, path), timeout=60)
    if response.status_code == 404:
        raise ConfigError(f"{path} not found in {repo}@{ref}")
    response.raise_for_status()
    pointer = _lfs_pointer(response.content)
    return response.content if pointer is None else _download_lfs(session, repo, ref, *pointer)


def resolve_repo_revision(repo: str, ref: str) -> str:
    result = subprocess.run(
        ["git", "ls-remote", f"https://github.com/{repo}.git", f"refs/heads/{ref}"],
        text=True, check=True, capture_output=True,
    )
    fields = result.stdout.split()
    if len(fields) != 2 or not re.fullmatch(r"[0-9a-fA-F]{40}", fields[0]):
        raise ConfigError(f"cannot resolve repository revision for {repo}@{ref}")
    return fields[0].lower()


def resolve_default_branch(repo: str) -> str:
    result = subprocess.run(
        ["git", "ls-remote", "--symref", f"https://github.com/{repo}.git", "HEAD"],
        text=True, check=True, capture_output=True,
    )
    for line in result.stdout.splitlines():
        match = re.fullmatch(r"ref:\s+refs/heads/(\S+)\s+HEAD", line)
        if match:
            return match.group(1)
    raise ConfigError(f"cannot determine default branch for {repo}")


def relative_includes(data: bytes) -> list[str]:
    text = data.decode("utf-8", errors="replace")
    includes = []
    patterns = (
        r"(?im)^\s*\.include\s+['\"]?([^\s'\"]+)",
        r"(?m)^\s*`include\s+\"([^\"]+)\"",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            value = match.group(1)
            if not value.startswith(("/", "$")):
                includes.append(value)
    return includes


def _repo_path(value: str, config: dict[str, Any]) -> str | None:
    resolved = resolve_json_variables(config)
    # Source files supplied by the PDK/LVS installation are not participant downloads.
    raw = value.strip()
    value = raw.replace("${UPRJ_ROOT}", "$UPRJ_ROOT")
    # Resolve project-defined variables while retaining UPRJ_ROOT.
    candidate = None
    for key in SOURCE_KEYS:
        for original, final in zip(_as_list(config.get(key), key), _as_list(resolved.get(key), key)):
            if original == raw:
                candidate = final.replace("${UPRJ_ROOT}", "$UPRJ_ROOT")
                break
    candidate = (candidate or value).replace("${UPRJ_ROOT}", "$UPRJ_ROOT")
    if candidate.startswith("./") or (candidate and not candidate.startswith(("/", "$"))):
        candidate = "$UPRJ_ROOT/" + candidate.removeprefix("./")
    if "$UPRJ_ROOT" not in candidate:
        return None
    if not candidate.startswith("$UPRJ_ROOT/") or VARIABLE_RE.search(candidate.removeprefix("$UPRJ_ROOT/")):
        raise ConfigError(f"project source path must resolve below $UPRJ_ROOT: {raw}")
    relative = candidate.removeprefix("$UPRJ_ROOT/")
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts or not relative:
        raise ConfigError(f"unsafe repository source path: {raw}")
    if any(char in relative for char in "*?["):
        raise ConfigError(f"repository source path may not use globbing: {raw}")
    return relative


def _recreate_data_root(data_root: Path) -> None:
    if data_root.exists():
        resolved_root = data_root.resolve()
        if resolved_root == Path(resolved_root.anchor) or resolved_root == Path.cwd().resolve():
            raise ConfigError(f"refusing to remove unsafe data root {data_root}")
        if data_root.is_symlink() or not data_root.is_dir():
            raise ConfigError(f"data root must be a real directory: {data_root}")
        retired = data_root.with_name(
            f".{data_root.name}.chipathon-remove-{uuid.uuid4().hex}"
        )
        os.replace(data_root, retired)
        try:
            shutil.rmtree(retired)
        except BaseException:
            # Restore the original directory if deletion did not complete and
            # the requested path is still available.
            if retired.exists() and not data_root.exists():
                os.replace(retired, data_root)
            raise
    data_root.mkdir(parents=True)


def _download_project(
    session: requests.Session, repo: Repository, data_root: Path,
    report_fields: dict[str, str] | None = None,
) -> dict[str, Any]:
    report_fields = report_fields if report_fields is not None else {}
    team_root = data_root / repo.team
    print(f"[{repo.team}] locating {repo.name}:info.yaml", flush=True)
    branch = "main"
    try:
        get_repo_file(session, repo.name, branch, "info.yaml")
    except ConfigError:
        branch = resolve_default_branch(repo.name)
        if branch == "main":
            raise
        get_repo_file(session, repo.name, branch, "info.yaml")
    revision = resolve_repo_revision(repo.name, branch)

    print(f"[{repo.team}] downloading {repo.name}@{revision}:info.yaml -> {team_root / 'info.yaml'}", flush=True)
    info_data = get_repo_file(session, repo.name, revision, "info.yaml")
    team_root.mkdir(parents=True, exist_ok=True)
    (team_root / "info.yaml").write_bytes(info_data)
    try:
        info = yaml.safe_load(info_data)
    except yaml.YAMLError as exc:
        raise ConfigError(f"malformed info.yaml: {exc}") from exc
    if not isinstance(info, dict):
        raise ConfigError("info.yaml top level is not a mapping")

    project = info.get("project")
    if not isinstance(project, dict):
        raise ConfigError("info.yaml project must be a mapping")

    report_fields["export_control"] = "none"
    lvs_entry = project.get("lvs_config")
    if isinstance(lvs_entry, str) and lvs_entry.strip():
        report_fields["lvs_config"] = "NOT FOUND: " + lvs_entry.strip()

    export_control = None
    if "export_control" in project:
        export_reference = project["export_control"]
        report_fields["export_control"] = "invalid: " + str(export_reference)
        if not isinstance(export_reference, str) or not export_reference.strip():
            raise ConfigError(
                "info.yaml project.export_control must be a nonempty "
                "repository-relative path"
            )
        export_reference = _safe_repo_relative(
            export_reference, "project.export_control"
        )
        report_fields["export_control"] = "NOT FOUND: " + export_reference
        export_path = (
            data_root / "export_control"
            / f"{repo.team}_{PurePosixPath(export_reference).name}"
        )
        print(
            f"[{repo.team}] downloading {repo.name}@{revision}:"
            f"{export_reference} -> {export_path}",
            flush=True,
        )
        export_data = get_repo_file(
            session, repo.name, revision, export_reference
        )
        export_path.parent.mkdir(parents=True, exist_ok=True)
        export_path.write_bytes(export_data)
        report_fields["export_control"] = str(export_path.resolve())
        export_control = {
            "repository_path": export_reference,
            "local_path": str(export_path),
            "size": len(export_data),
            "sha256": hashlib.sha256(export_data).hexdigest(),
        }

    lvs_reference = project.get("lvs_config")
    if not isinstance(lvs_reference, str) or not lvs_reference.strip():
        raise ConfigError("info.yaml has no project.lvs_config")
    lvs_reference = _safe_repo_relative(lvs_reference, "project.lvs_config")
    print(f"[{repo.team}] downloading {repo.name}@{revision}:{lvs_reference} -> {team_root / lvs_reference}", flush=True)
    lvs_data = get_repo_file(session, repo.name, revision, lvs_reference)
    lvs_path = team_root / lvs_reference
    lvs_path.parent.mkdir(parents=True, exist_ok=True)
    lvs_path.write_bytes(lvs_data)
    report_fields["lvs_config"] = str(lvs_path.resolve())
    try:
        config = json.loads(lvs_data)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"malformed lvs_config.json: {exc}") from exc
    if not isinstance(config, dict):
        raise ConfigError("lvs_config.json top level is not a JSON object")
    _reject_deprecated_sources(config)

    downloaded_paths: set[str] = set()
    source_files: list[dict[str, Any]] = []

    def download_source(relative: str, *, spice: bool) -> None:
        if relative in downloaded_paths:
            return
        destination = team_root / relative
        print(f"[{repo.team}] downloading {repo.name}@{revision}:{relative} -> {destination}", flush=True)
        data = get_repo_file(session, repo.name, revision, relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        downloaded_paths.add(relative)
        source_files.append({
            "repository_path": relative, "local_path": str(destination),
            "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
        })
        if spice:
            pattern = r'''(?im)^\s*\.(?:include|inc)\s+(?:"([^"]+)"|'([^']+)'|([^\s]+))'''
            for match in re.finditer(pattern, data.decode("utf-8", errors="replace")):
                included = next(group for group in match.groups() if group is not None)
                if included.startswith(("$UPRJ_ROOT/", "${UPRJ_ROOT}/")):
                    child = _safe_repo_relative(included, f"include in {relative}")
                elif included.startswith(("/", "$")):
                    continue
                else:
                    child = _safe_repo_relative(
                        posixpath.normpath(posixpath.join(posixpath.dirname(relative), included)),
                        f"include in {relative}",
                    )
                download_source(child, spice=True)
        elif relative_includes(data):
            raise ConfigError(
                f"{relative} contains unsupported relative include(s): "
                + ", ".join(relative_includes(data))
            )

    for key in SOURCE_KEYS:
        for value in _as_list(config.get(key), key):
            relative = _repo_path(value, config)
            if relative is None or relative in downloaded_paths:
                continue
            download_source(relative, spice=key == "LVS_SPICE_FILES")

    layout = resolve_json_variables(config).get("LAYOUT_FILE")
    if not isinstance(layout, str):
        raise ConfigError("invalid LAYOUT_FILE")
    layout = layout.replace("${UPRJ_ROOT}", "$UPRJ_ROOT")
    if layout.startswith("./") or (layout and not layout.startswith(("/", "$"))):
        layout = "$UPRJ_ROOT/" + layout.removeprefix("./")
    if not layout.startswith("$UPRJ_ROOT/"):
        raise ConfigError("LAYOUT_FILE must be repository-relative or begin with $UPRJ_ROOT/")
    layout_relative = _safe_repo_relative(
        layout.removeprefix("$UPRJ_ROOT/"), "LAYOUT_FILE"
    )
    layout_path = team_root / layout_relative
    print(f"[{repo.team}] downloading {repo.name}@{revision}:{layout_relative} -> {layout_path}", flush=True)
    gds_data = get_repo_file(session, repo.name, revision, layout_relative)
    layout_path.parent.mkdir(parents=True, exist_ok=True)
    layout_path.write_bytes(gds_data)
    return {
        "team": repo.team, "repository": repo.name, "ref": branch,
        "revision": revision,
        "top_layout": resolve_json_variables(config).get("TOP_LAYOUT", ""),
        "info": {"repository_path": "info.yaml",
                 "local_path": str(team_root / "info.yaml"),
                 "size": len(info_data),
                 "sha256": hashlib.sha256(info_data).hexdigest()},
        "export_control": export_control,
        "lvs_config": {"repository_path": lvs_reference,
                       "local_path": str(lvs_path), "size": len(lvs_data),
                       "sha256": hashlib.sha256(lvs_data).hexdigest()},
        "source_files": sorted(source_files, key=lambda item: item["repository_path"]),
        "layout_file": {"repository_path": layout_relative,
                        "local_path": str(layout_path), "size": len(gds_data),
                        "sha256": hashlib.sha256(gds_data).hexdigest()},
    }


def download_projects(
    repo_list: Path, data_root: Path, *, assume_yes: bool = False,
    input_fn=input, session: requests.Session | None = None,
    start_callback: Callable[[], None] | None = None,
) -> dict[str, Any]:
    repositories = tuple(load_repo_list(repo_list).values())
    if not repositories:
        raise ConfigError(f"{repo_list}: no project repositories")
    if data_root.exists() and not assume_yes:
        prompt = f"Remove and recreate the complete data directory?\n  {data_root}\nType 'yes' to continue: "
        if input_fn(prompt).strip().casefold() != "yes":
            raise ConfigError("data-directory removal declined; download aborted")
    if start_callback is not None:
        start_callback()
    _recreate_data_root(data_root)
    session = session or requests.Session()
    session.headers.update({"User-Agent": "chipathon-2026-fullchip-lvs"})
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        session.headers["Authorization"] = f"Bearer {token}"
    projects = []
    failures = []
    for repo in repositories:
        team_root = data_root / repo.team
        failed_root = data_root / f"{repo.team}_failed"
        report_fields = {"export_control": "unknown", "lvs_config": "unknown"}
        try:
            projects.append(_download_project(session, repo, data_root, report_fields))
        except (ConfigError, OSError, requests.RequestException,
                subprocess.CalledProcessError, ValueError) as exc:
            message = str(exc)
            disposition = (
                f"preserving partial data as {failed_root}"
                if team_root.exists()
                else "no project directory was created"
            )
            print(f"[{repo.team}] ERROR: {message}; {disposition}",
                  file=sys.stderr, flush=True)
            if team_root.exists():
                if failed_root.exists():
                    raise ConfigError(
                        f"cannot preserve failed project because destination exists: "
                        f"{failed_root}"
                    )
                os.replace(team_root, failed_root)
                lvs_report = report_fields["lvs_config"]
                if lvs_report.startswith(str(team_root.resolve()) + os.sep):
                    report_fields["lvs_config"] = str(
                        failed_root.resolve() / Path(lvs_report).relative_to(team_root.resolve())
                    )
            export_root = data_root / "export_control"
            prefix = f"{repo.team}_"
            retained_export_control = sorted(
                str(candidate)
                for candidate in export_root.iterdir()
                if candidate.is_file() and candidate.name.startswith(prefix)
            ) if export_root.is_dir() else []
            failures.append({
                "team": repo.team,
                "repository": repo.name,
                "error": message,
                "failed_path": str(failed_root) if failed_root.exists() else None,
                "retained_export_control": retained_export_control,
                "info": {"local_path": str(failed_root / "info.yaml")}
                if (failed_root / "info.yaml").is_file() else None,
                "report_fields": report_fields,
            })
    return {
        "schema_version": 1, "repo_list": str(repo_list),
        "status": "partial" if failures else "complete",
        "projects": projects, "failures": failures,
        "repository_order": [repo.team for repo in repositories],
    }


def ensure_lvs_root(path: Path) -> str:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--depth", "1", "--branch", LVS_BRANCH, LVS_REPOSITORY, str(path)], check=True)
    if not (path / ".git").exists():
        raise ConfigError(f"LVS_ROOT is not a Git checkout: {path}")
    remote = subprocess.run(["git", "-C", str(path), "config", "--get", "remote.origin.url"], check=True, text=True, capture_output=True).stdout.strip()
    branch = subprocess.run(["git", "-C", str(path), "branch", "--show-current"], check=True, text=True, capture_output=True).stdout.strip()
    normalized_remote = remote.rstrip("/").removesuffix(".git").replace("git@github.com:", "https://github.com/")
    if not normalized_remote.endswith("github.com/d-m-bailey/extra_be_checks"):
        raise ConfigError(f"LVS_ROOT has unexpected origin {remote!r}: {path}")
    if branch != LVS_BRANCH:
        raise ConfigError(f"LVS_ROOT must use branch {LVS_BRANCH}, found {branch or 'detached HEAD'}")
    return subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], check=True, text=True, capture_output=True).stdout.strip()


def _rewrite_source(value: str, config: dict[str, Any], team: str, team_root: Path) -> str:
    relative = _repo_path(value, config)
    if relative is None:
        return value
    local = team_root / relative
    if not local.is_file():
        raise ConfigError(f"{team}: downloaded source file is missing: {local}")
    return f"${team.upper()}_ROOT/{relative}"


def _source_definitions(path: Path) -> list[tuple[str, str]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    definitions = [(match.group(1), "verilog") for match in re.finditer(r"(?im)^\s*module\s+([\\A-Za-z_][\\\w$]*)", text)]
    definitions.extend((match.group(1), "spice") for match in re.finditer(r"(?im)^\s*\.subckt\s+(\S+)", text))
    definitions.extend((match.group(1), "model") for match in re.finditer(r"(?im)^\s*\.model\s+(\S+)", text))
    return definitions


def _verilog_ports(text: str, module: str) -> list[str] | None:
    clean = re.sub(r"/\*.*?\*/|//[^\n]*", "", text, flags=re.S)
    match = re.search(rf"\bmodule\s+{re.escape(module)}\s*(?:#\s*\(.*?\)\s*)?\((.*?)\)\s*;", clean, re.S)
    if not match:
        return None
    ports = []
    for item in match.group(1).split(","):
        names = re.findall(r"[A-Za-z_][\w$]*(?:\s*\[[^]]+\])?", re.sub(r"\[[^]]+\]", "", item))
        names = [name for name in names if name not in {"input", "output", "inout", "wire", "reg", "logic", "signed", "unsigned"}]
        if names:
            ports.append(names[-1])
    return ports


def _spice_ports(text: str, subckt: str) -> list[str] | None:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = re.match(rf"(?i)^\s*\.subckt\s+{re.escape(subckt)}\s*(.*)$", line)
        if match:
            value = match.group(1)
            while index + 1 < len(lines) and lines[index + 1].lstrip().startswith("+"):
                index += 1
                value += " " + lines[index].lstrip()[1:]
            return value.split()
    return None


def _write_wrapper(path: Path, integrated: str, source: str, source_files: Iterable[Path]) -> str:
    matches = []
    for source_file in source_files:
        text = source_file.read_text(encoding="utf-8", errors="replace")
        ports = _verilog_ports(text, source)
        if ports is not None:
            matches.append(("verilog", ports, source_file))
        ports = _spice_ports(text, source)
        if ports is not None:
            matches.append(("spice", ports, source_file))
    if len(matches) != 1:
        raise ConfigError(f"{source}: expected exactly one source top definition, found {len(matches)}")
    language, ports, _ = matches[0]
    if language == "verilog":
        path = path.with_suffix(".v")
        connections = ", ".join(f".{port}({port})" for port in ports)
        path.write_text(f"module {integrated} ({', '.join(ports)});\n  inout {', '.join(ports)};\n  {source} wrapped_project ({connections});\nendmodule\n", encoding="utf-8")
    else:
        path = path.with_suffix(".spice")
        path.write_text(f".subckt {integrated} {' '.join(ports)}\nXwrapped_project {' '.join(ports)} {source}\n.ends {integrated}\n", encoding="utf-8")
    return str(path)


def generate_lvs_configs(
    integration_yaml: Path, data_root: Path, integrated_root: Path,
    output_root: Path, lvs_root: Path, pdk_root: Path, pdk: str,
    *, base_chip: Path | None = None,
) -> dict[str, Any]:
    chip = load_chip_request(integration_yaml, base_chip_override=base_chip)
    io_config = lvs_root / "tech" / pdk / "lvs_config.io.json"
    if not io_config.is_file():
        raise ConfigError(f"missing required I/O LVS configuration: {io_config}")
    placement_path = integrated_root / f"{chip.name}_placement_manifest.json"
    placement = json.loads(placement_path.read_text(encoding="utf-8"))
    placement_by_team = {item["team"].casefold(): item for item in placement.get("projects", [])}
    output_root.mkdir(parents=True, exist_ok=True)
    configs_dir, wrappers_dir = output_root / "configs", output_root / "wrappers"
    configs_dir.mkdir(exist_ok=True)
    wrappers_dir.mkdir(exist_ok=True)
    project_configs, definitions, wrappers = [], {}, []
    all_spice, all_verilog = [], []
    participant_sources: set[Path] = set()
    standard_libraries = set()
    team_roots: dict[str, Path] = {}
    for request in chip.projects:
        team, team_upper = request.team, request.team.upper()
        _info_path, lvs_path, _gds_path = resolve_project_data(data_root, team)
        original = load_lvs_config(lvs_path)
        _reject_deprecated_sources(original)
        team_root = data_root / team
        team_roots[team_upper] = team_root.resolve()
        library = original.get("STD_CELL_LIBRARY")
        if isinstance(library, str) and library:
            standard_libraries.add(library)
        fragment: dict[str, Any] = {}
        local_sources: list[Path] = []
        for key in LIST_KEYS:
            values = _as_list(original.get(key), key)
            rewritten = []
            for value in values:
                new_value = _rewrite_source(value, original, team, team_root)
                if new_value not in rewritten:
                    rewritten.append(new_value)
                if new_value.startswith(f"${team_upper}_ROOT/"):
                    local_sources.append(team_root / new_value.split("/", 1)[1])
            if rewritten:
                fragment[key] = rewritten
        project_config = configs_dir / f"{team}_lvs_config.json"
        json_dump(project_config, fragment)
        project_configs.append(project_config)
        participant_sources.update(path.resolve() for path in local_sources)
        for source_file in local_sources:
            for name, kind in _source_definitions(source_file):
                definitions.setdefault(name, []).append((team, str(source_file), kind))
        details = placement_by_team.get(team.casefold())
        if details is None:
            raise ConfigError(f"{team}: missing from placement manifest {placement_path}")
        source_top, integrated_top = details.get("source_top"), details.get("integrated_top")
        if not isinstance(source_top, str) or not isinstance(integrated_top, str):
            raise ConfigError(f"{team}: placement manifest has invalid top-cell names")
        top_definitions = [
            (kind, source_file) for source_file in local_sources
            for name, kind in _source_definitions(source_file) if name == source_top
        ]
        if len(top_definitions) != 1:
            raise ConfigError(f"{team}: expected exactly one source definition for {source_top}, found {len(top_definitions)}")
        if source_top != integrated_top:
            wrapper = Path(_write_wrapper(wrappers_dir / team, integrated_top, source_top, local_sources))
            wrappers.append({"team": team, "source_top": source_top, "integrated_top": integrated_top,
                             "path": str(wrapper), "sha256": file_sha256(wrapper)})
            (all_verilog if wrapper.suffix == ".v" else all_spice).append(str(wrapper.resolve()))
    conflicts = []
    standard_prefixes = tuple(f"{name}__" for name in standard_libraries)
    for name, owners in definitions.items():
        teams = {owner[0].casefold() for owner in owners}
        if len(teams) > 1 and not name.startswith(standard_prefixes):
            conflicts.append({"name": name, "definitions": owners})
    if conflicts:
        summary = "; ".join(f"{item['name']}: {', '.join(sorted({d[0] for d in item['definitions']}))}" for item in conflicts)
        raise ConfigError(f"cross-project source definition conflicts: {summary}")
    ring_v = (integrated_root / f"{chip.name}_padring.v").resolve()
    top_v = (integrated_root / f"{chip.name}.v").resolve()
    layout = (integrated_root / f"{chip.name}.gds").resolve()
    for required in (ring_v, top_v, layout):
        if not required.is_file():
            raise ConfigError(f"missing integrated LVS input: {required}")
    top_config = output_root / f"{chip.name}_lvs_config.json"
    integration_var = "$INTEGRATION_ROOT"
    build_var = "$LVS_BUILD_ROOT"
    def local_source(path: str) -> str:
        source = Path(path).resolve()
        try:
            return f"{build_var}/{source.relative_to(output_root.resolve()).as_posix()}"
        except ValueError:
            return str(source)
    data = {
        "INTEGRATION_ROOT": str(integrated_root.resolve()),
        "LVS_BUILD_ROOT": str(output_root.resolve()),
        **{f"{team}_ROOT": str(path) for team, path in team_roots.items()},
        "INCLUDE_CONFIGS": [
            "$LVS_ROOT/tech/$PDK/lvs_config.io.json",
            *[f"{build_var}/configs/{path.name}" for path in project_configs],
        ],
        "TOP_SOURCE": chip.name, "TOP_LAYOUT": chip.name,
        "LAYOUT_FILE": f"{integration_var}/{chip.name}.gds",
        "LVS_SPICE_FILES": [local_source(path) for path in all_spice],
        "LVS_VERILOG_FILES": [
            *[local_source(path) for path in all_verilog],
            f"{integration_var}/{chip.name}_padring.v", f"{integration_var}/{chip.name}.v",
        ],
    }
    json_dump(top_config, data)
    environment = {
        "LVS_ROOT": str(lvs_root.resolve()), "PDK_ROOT": str(pdk_root.resolve()), "PDK": pdk,
        "WORK_ROOT": str((output_root / "work").resolve()),
        "LOG_ROOT": str((output_root / "logs").resolve()),
        "SIGNOFF_ROOT": str((output_root / "signoff").resolve()),
        "INTEGRATION_ROOT": str(integrated_root.resolve()),
        "LVS_BUILD_ROOT": str(output_root.resolve()),
        **{f"{team}_ROOT": str(path) for team, path in team_roots.items()},
    }
    env_path = output_root / f"{chip.name}_lvs.env"
    env_path.write_text(render_environment(environment), encoding="utf-8")
    manifest = {
        "schema_version": 1, "chip_name": chip.name, "top_config": str(top_config),
        "io_config": str(io_config), "project_configs": [str(path) for path in project_configs],
        "wrappers": wrappers, "standard_cell_libraries": sorted(standard_libraries),
        "source_name_conflicts": conflicts,
        "environment_file": str(env_path), "environment": environment,
        "hashes": {
            str(path): file_sha256(path)
            for path in (
                io_config, top_config, env_path, *project_configs,
                *sorted(participant_sources | {path.resolve() for path in (
                    *[Path(value) for value in all_spice],
                    *[Path(value) for value in all_verilog],
                )}, key=str),
                ring_v, top_v, layout,
            )
        },
    }
    json_dump(output_root / f"{chip.name}_lvs_manifest.json", manifest)
    return manifest


def render_environment(environment: dict[str, str]) -> str:
    return "# Generated by chipathon2026 integration; do not edit.\n" + "".join(
        f"export {key}={shlex.quote(value)}\n" for key, value in environment.items()
    )


def run_lvs(config: Path, lvs_root: Path, work_root: Path, log_root: Path, signoff_root: Path,
            pdk_root: Path, pdk: str, *, environment: dict[str, str] | None = None) -> int:
    env = os.environ.copy()
    effective = {"LVS_ROOT": str(lvs_root.resolve()), "WORK_ROOT": str(work_root.resolve()),
                "LOG_ROOT": str(log_root.resolve()), "SIGNOFF_ROOT": str(signoff_root.resolve()),
                "PDK_ROOT": str(pdk_root.resolve()), "PDK": pdk}
    if environment:
        for key, expected in effective.items():
            if environment.get(key) != expected:
                raise ConfigError(
                    f"generated LVS environment {key}={environment.get(key)!r} "
                    f"does not match requested value {expected!r}"
                )
        effective.update(environment)
    env.update(effective)
    for directory in (work_root, log_root, signoff_root):
        directory.mkdir(parents=True, exist_ok=True)
    preflight = subprocess.run(
        [str(lvs_root / "set_lvs_env.py"), "-c", str(config.resolve())],
        env=env, text=True, capture_output=True,
    )
    if preflight.returncode:
        raise ConfigError(f"LVS environment preflight failed: {preflight.stderr.strip()}")
    result = subprocess.run([str(lvs_root / "run_full_lvs"), str(config.resolve())], env=env)
    return result.returncode
