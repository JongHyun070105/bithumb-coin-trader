#!/usr/bin/env python3
"""Import an original local download into the ignored external research lane.

Usage: python scripts/prepare_external_bitmex_dataset.py --input /path/to/download.zip
Pass all source files in one invocation: the source manifest is immutable.
No network, AWS, trading API, or Git staging operations are performed.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import zipfile
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from bithumb_coin_trader.research_infra.external_expert import profile_csv, sha256_file  # noqa: E402
from bithumb_coin_trader.research_infra.registry import EXTERNAL_BITMEX_DATASET_ID  # noqa: E402

SOURCE_PAGE = "https://gall.dcinside.com/mgallery/board/view/?id=chartanalysis&no=5051684"
DOWNLOAD_PAGE = "https://drive.google.com/file/d/1XDwxbriz_kOq44iH-mHcjsYTBklMnMW3/view?usp=sharing"
MAX_EXTRACTED_BYTES = 2_000_000_000


def _require_ignored(path: Path, repository_root: Path) -> None:
    result = subprocess.run(
        ["git", "check-ignore", "-q", str(path)],
        cwd=repository_root, check=False,
    )
    if result.returncode != 0:
        raise ValueError(f"Raw data destination is not gitignored: {path}")


def _copy_new(source: Path, destination: Path) -> None:
    if destination.is_symlink():
        raise ValueError(f"Raw destination is a symlink: {destination}")
    if destination.exists():
        if sha256_file(source) != sha256_file(destination):
            raise FileExistsError(f"Existing raw file differs: {destination}")
        return
    with source.open("rb") as src, destination.open("xb") as dst:
        shutil.copyfileobj(src, dst, 1024 * 1024)


def _extract_zip(archive: Path, raw_dir: Path) -> list[Path]:
    extracted: list[Path] = []
    with zipfile.ZipFile(archive) as zipped:
        members = [member for member in zipped.infolist() if not member.is_dir()]
        if sum(member.file_size for member in members) > MAX_EXTRACTED_BYTES:
            raise ValueError("Archive exceeds local extraction limit")
        names = [Path(member.filename).name for member in members]
        if len(names) != len(set(names)) or archive.name in names:
            raise ValueError("Archive contains colliding filenames")
        for member in members:
            name = Path(member.filename)
            mode = member.external_attr >> 16
            if (name.is_absolute() or ".." in name.parts or not name.name
                    or stat.S_IFMT(mode) not in (0, stat.S_IFREG)):
                raise ValueError(f"Unsafe archive member: {member.filename}")
        for member in members:
            destination = raw_dir / Path(member.filename).name
            if destination.is_symlink():
                raise ValueError(f"Raw destination is a symlink: {destination}")
            if destination.exists():
                # Stream into no temporary path: verify existing bytes against the member.
                import hashlib
                digest = hashlib.sha256()
                with zipped.open(member) as source:
                    for block in iter(lambda: source.read(1024 * 1024), b""):
                        digest.update(block)
                if digest.hexdigest() != sha256_file(destination):
                    raise FileExistsError(f"Existing extracted file differs: {destination}")
            else:
                with zipped.open(member) as source, destination.open("xb") as target:
                    shutil.copyfileobj(source, target, 1024 * 1024)
            extracted.append(destination)
    return extracted


def _write_new_json(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8") as target:
        json.dump(value, target, indent=2, sort_keys=True, ensure_ascii=False)
        target.write("\n")


def _write_profile(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def validate_source_manifest(manifest: object) -> None:
    if not isinstance(manifest, dict):
        raise ValueError("Source manifest must be an object")
    required = {
        "dataset_id": EXTERNAL_BITMEX_DATASET_ID,
        "schema_version": "1",
        "source_class": "EXTERNAL_EXPERT_BEHAVIOR_DATASET",
        "claimed_author": "워뇨띠",
        "author_identity_verified": False,
        "primary_use": "HYPOTHESIS_GENERATION_ONLY",
        "raw_immutable": True,
    }
    for key, expected in required.items():
        if manifest.get(key) != expected:
            raise ValueError(f"Invalid source manifest field: {key}")
    files = manifest.get("source_files")
    if not isinstance(files, list) or not files:
        raise ValueError("Source manifest must list imported files")
    names = set()
    for item in files:
        if not isinstance(item, dict):
            raise ValueError("Source file entry must be an object")
        for field in (
            "dataset_id", "source_class", "source_reference", "original_filename",
            "sha256", "size_bytes", "imported_at_utc", "claimed_coverage_start",
            "claimed_coverage_end", "parser_status", "row_count", "schema_fingerprint",
        ):
            if field not in item:
                raise ValueError(f"Missing source file field: {field}")
        name = item["original_filename"]
        if not isinstance(name, str) or Path(name).name != name or name in names:
            raise ValueError("Unsafe or duplicate source filename")
        names.add(name)
        if (item["dataset_id"] != required["dataset_id"]
                or item["source_class"] != required["source_class"]
                or not isinstance(item["sha256"], str)
                or len(item["sha256"]) != 64
                or any(char not in "0123456789abcdef" for char in item["sha256"])
                or not isinstance(item["size_bytes"], int) or item["size_bytes"] < 0):
            raise ValueError("Invalid source file identity")
        if item["parser_status"] == "PROFILED":
            if not isinstance(item["row_count"], int) or not isinstance(item["schema_fingerprint"], str):
                raise ValueError("Profiled CSV needs row count and schema fingerprint")
        elif item["parser_status"] != "NOT_APPLICABLE" or item["row_count"] is not None or item["schema_fingerprint"] is not None:
            raise ValueError("Invalid parser status")


def prepare_dataset(
    inputs: list[Path], *, repository_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    if not inputs:
        raise ValueError("At least one local source path is required")
    root = repository_root / ".external-research-data" / EXTERNAL_BITMEX_DATASET_ID
    raw = root / "raw"
    _require_ignored(raw / "source.csv", repository_root)
    for path in (raw, root / "normalized", root / "verification", root / "cache"):
        path.mkdir(parents=True, exist_ok=True)
    manifest_path = root / "source-manifest.json"
    input_paths = [path.resolve(strict=True) for path in inputs]
    if any(not path.is_file() or path.is_symlink() for path in input_paths):
        raise ValueError("Inputs must be regular local files, not symlinks")
    if len({path.name for path in input_paths}) != len(input_paths):
        raise ValueError("Input filenames must be unique")
    if manifest_path.exists():
        saved = json.loads(manifest_path.read_text(encoding="utf-8"))
        validate_source_manifest(saved)
        source_files = saved["source_files"]
        if {path.name for path in input_paths} != {item["original_filename"] for item in source_files if item["source_reference"] == DOWNLOAD_PAGE}:
            raise ValueError("Manifest is sealed; a new import requires a new dataset identity")
        for item in source_files:
            target = raw / item["original_filename"]
            if not target.is_file() or sha256_file(target) != item["sha256"]:
                raise ValueError(f"Imported source file is missing or changed: {target}")
        return saved
    imported_at = datetime.now(timezone.utc).isoformat()
    copied: list[tuple[Path, str]] = []
    seen: set[str] = set()
    for source in input_paths:
        target = raw / source.name
        _copy_new(source, target)
        copied.append((target, DOWNLOAD_PAGE))
        seen.add(target.name)
        if zipfile.is_zipfile(target):
            for extracted in _extract_zip(target, raw):
                if extracted.name in seen:
                    raise ValueError("Imported files have colliding names")
                copied.append((extracted, f"{source.name}:{extracted.name}"))
                seen.add(extracted.name)
    profiles = []
    files = []
    for path, reference in copied:
        is_csv = path.suffix.lower() in (".csv", ".tsv")
        profile = profile_csv(path) if is_csv else None
        if profile:
            profiles.append(profile)
        files.append({
            "dataset_id": EXTERNAL_BITMEX_DATASET_ID,
            "source_class": "EXTERNAL_EXPERT_BEHAVIOR_DATASET",
            "source_reference": reference,
            "original_filename": path.name,
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
            "imported_at_utc": imported_at,
            "claimed_coverage_start": "2018-03",
            "claimed_coverage_end": "2021-12",
            "parser_status": "PROFILED" if profile else "NOT_APPLICABLE",
            "row_count": profile["row_count"] if profile else None,
            "schema_fingerprint": profile["schema_fingerprint"] if profile else None,
        })
    manifest: dict[str, Any] = {
        "dataset_id": EXTERNAL_BITMEX_DATASET_ID,
        "schema_version": "1",
        "source_class": "EXTERNAL_EXPERT_BEHAVIOR_DATASET",
        "claimed_author": "워뇨띠",
        "author_identity_verified": False,
        "primary_use": "HYPOTHESIS_GENERATION_ONLY",
        "raw_immutable": True,
        "public_release_page": SOURCE_PAGE,
        "public_download_page": DOWNLOAD_PAGE,
        "source_files": files,
    }
    validate_source_manifest(manifest)
    _write_profile(root / "verification" / "schema-profile.json", {
        "dataset_id": EXTERNAL_BITMEX_DATASET_ID,
        "profiled_at_utc": imported_at,
        "files": profiles,
    })
    _write_new_json(manifest_path, manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, action="append", required=True,
                        help="Original local download; repeat for multiple files")
    args = parser.parse_args()
    manifest = prepare_dataset(args.input)
    print(json.dumps({"manifest": str(REPO_ROOT / ".external-research-data" / EXTERNAL_BITMEX_DATASET_ID / "source-manifest.json"),
                      "source_files": manifest["source_files"]}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
