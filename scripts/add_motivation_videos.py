#!/usr/bin/env python3
"""Validate and add motivation videos to the repository library."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_MAX_BYTES = 95_000_000
DEFAULT_MIN_DURATION = 1.0
DEFAULT_MAX_DURATION = 180.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True, help="Directory containing source MP4 files")
    parser.add_argument("--metadata-file", type=Path, help="Optional JSON map keyed by input filename")
    parser.add_argument("--source-url", help="Default source URL for every added file")
    parser.add_argument("--license-url", help="Default license URL for every added file")
    parser.add_argument("--source-type", default="user-provided", help="Default source type stored in the manifest")
    parser.add_argument("--license-reviewed-at", help="Default ISO date/time when the license was reviewed")
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--min-duration", type=float, default=DEFAULT_MIN_DURATION)
    parser.add_argument("--max-duration", type=float, default=DEFAULT_MAX_DURATION)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def probe(path: Path) -> dict[str, Any]:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration,size:stream=codec_type,codec_name,width,height",
        "-of",
        "json",
        str(path),
    ]
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        payload = json.loads(result.stdout)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        raise ValueError(f"ffprobe failed for {path.name}: {exc}") from exc

    streams = payload.get("streams", [])
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    if not video or video.get("codec_name") != "h264":
        raise ValueError(f"{path.name}: expected an H.264 video stream")
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    duration = float(payload.get("format", {}).get("duration") or 0)
    size = int(payload.get("format", {}).get("size") or path.stat().st_size)
    return {
        "duration_seconds": round(duration, 3),
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "video_codec": video["codec_name"],
        "audio_codec": audio.get("codec_name") if audio else None,
        "bytes": size,
    }


def load_metadata(path: Path | None) -> dict[str, dict[str, Any]]:
    if not path:
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("metadata JSON must be an object keyed by input filename")
    return {str(key): value for key, value in raw.items() if isinstance(value, dict)}


def next_video_number(records: list[dict[str, Any]]) -> int:
    numbers = []
    for record in records:
        name = Path(str(record.get("asset", ""))).stem
        if name.startswith("video_") and name[6:].isdigit():
            numbers.append(int(name[6:]))
    return max(numbers, default=0) + 1


def main() -> int:
    args = parse_args()
    input_dir = args.input_dir.expanduser().resolve()
    repo_root = args.repo_root.expanduser().resolve()
    video_dir = repo_root / "assets" / "motivation_videos"
    manifest_path = repo_root / "assets" / "motivation_videos_manifest.json"
    if not input_dir.is_dir():
        raise ValueError(f"Input directory does not exist: {input_dir}")
    video_dir.mkdir(parents=True, exist_ok=True)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else []
    if not isinstance(manifest, list):
        raise ValueError("Existing motivation video manifest must be a JSON array")
    existing_hashes = {record.get("sha256") for record in manifest}
    metadata = load_metadata(args.metadata_file)
    candidates = sorted(path for path in input_dir.iterdir() if path.is_file() and path.suffix.lower() == ".mp4")
    if not candidates:
        raise ValueError(f"No MP4 files found in {input_dir}")

    additions: list[tuple[Path, dict[str, Any], str]] = []
    seen_hashes: set[str] = set()
    for source in candidates:
        info = probe(source)
        if info["bytes"] > args.max_bytes:
            raise ValueError(f"{source.name}: {info['bytes']} bytes exceeds --max-bytes={args.max_bytes}")
        if not args.min_duration <= info["duration_seconds"] <= args.max_duration:
            raise ValueError(
                f"{source.name}: duration {info['duration_seconds']}s is outside "
                f"{args.min_duration}–{args.max_duration}s"
            )
        digest = sha256(source)
        if digest in existing_hashes or digest in seen_hashes:
            print(f"SKIP duplicate: {source.name}")
            continue
        seen_hashes.add(digest)
        additions.append((source, info, digest))

    if not additions:
        print("No new videos to add.")
        return 0

    timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    number = next_video_number(manifest)
    staged: list[tuple[Path, Path]] = []
    new_records: list[dict[str, Any]] = []
    try:
        with tempfile.TemporaryDirectory(prefix="motivation-videos-", dir=video_dir) as temp_dir:
            staging_dir = Path(temp_dir)
            for source, info, digest in additions:
                while (video_dir / f"video_{number:03d}.mp4").exists():
                    number += 1
                final_path = video_dir / f"video_{number:03d}.mp4"
                staged_path = staging_dir / final_path.name
                shutil.copy2(source, staged_path)
                staged.append((staged_path, final_path))
                record: dict[str, Any] = {
                    "asset": str(final_path.relative_to(repo_root)).replace("\\", "/"),
                    "original_name": source.name,
                    "sha256": digest,
                    **info,
                    "added_at": timestamp,
                }
                file_metadata = metadata.get(source.name, metadata.get(source.stem, {}))
                merged = {
                    "source_type": args.source_type,
                    "source_url": args.source_url,
                    "license_url": args.license_url,
                    "license_reviewed_at": args.license_reviewed_at,
                    **file_metadata,
                }
                for key, value in merged.items():
                    if value not in (None, ""):
                        record[key] = value
                new_records.append(record)
                number += 1

            updated_manifest = manifest + new_records
            manifest_temp = manifest_path.with_suffix(".json.tmp")
            manifest_temp.write_text(
                json.dumps(updated_manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            for staged_path, final_path in staged:
                staged_path.replace(final_path)
            manifest_temp.replace(manifest_path)
    except Exception:
        for _, final_path in staged:
            final_path.unlink(missing_ok=True)
        manifest_path.with_suffix(".json.tmp").unlink(missing_ok=True)
        raise

    for record in new_records:
        print(f"ADDED {record['asset']} ({record['bytes']} bytes, {record['duration_seconds']}s)")
    print(f"Added {len(new_records)} video(s); manifest now contains {len(updated_manifest)} record(s).")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
