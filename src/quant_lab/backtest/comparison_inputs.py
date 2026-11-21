"""比較実験の入力固定と改変検知。"""

import hashlib
from dataclasses import replace
from pathlib import Path
from typing import Any

from .config import ExperimentConfig


def snapshot_inputs(
    base: ExperimentConfig, directory: Path
) -> tuple[ExperimentConfig, dict[str, Any]]:
    inputs = directory / "inputs"
    inputs.mkdir()
    source = Path(base.data.path).resolve()
    artifacts: dict[str, Any] = {}

    def snapshot(
        label: str, original: Path, target: Path, *, optional: bool = False
    ) -> None:
        payload = original.read_bytes() if not optional or original.is_file() else None
        artifacts[label] = dict(
            original_path=str(original),
            snapshot_path=str(target),
            sha256=hashlib.sha256(payload).hexdigest() if payload is not None else None,
        )
        if payload is not None:
            target.write_bytes(payload)

    saved = inputs / source.name
    snapshot("data", source, saved)
    snapshot(
        "metadata",
        source.with_name(source.stem + "_metadata.json"),
        saved.with_name(saved.stem + "_metadata.json"),
        optional=True,
    )
    quality_paths = {}
    for key in ("evidence_path", "daily_reference_path", "daily_metadata_path"):
        original = getattr(base.quality, key)
        if original is not None:
            target = inputs / f"{key}{Path(original).suffix}"
            snapshot(key, Path(original).resolve(), target)
            quality_paths[key] = str(target)

    base = replace(
        base,
        data=replace(base.data, path=str(saved)),
        quality=replace(base.quality, **quality_paths),
    )
    return base, artifacts


def verify_inputs(artifacts: dict[str, Any]) -> None:
    for artifact in artifacts.values():
        target = Path(artifact["snapshot_path"])
        actual = (
            hashlib.sha256(target.read_bytes()).hexdigest()
            if target.is_file()
            else None
        )
        if actual != artifact["sha256"]:
            raise ValueError("比較中に保存済み入力または品質根拠が変更されました")
