#!/usr/bin/env python3
"""List the repositories configured as Graphify scan targets."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class RepositorySpec:
    name: str
    path: Path
    url: str


def load_registry(path: Path) -> list[RepositorySpec]:
    registry_path = path.resolve()
    raw = yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}
    if raw.get("version") != 1:
        raise ValueError("repositories.yml must declare version: 1")

    repositories: list[RepositorySpec] = []
    seen_names: set[str] = set()
    seen_paths: set[Path] = set()
    for item in raw.get("repositories", []):
        name = str(item.get("name", "")).strip()
        configured_path = str(item.get("path", "")).strip()
        url = str(item.get("url", "")).strip()
        if not name or not configured_path:
            raise ValueError("Every repository needs non-empty name and path values")
        resolved_path = (registry_path.parent / configured_path).resolve()
        if name.casefold() in seen_names:
            raise ValueError(f"Duplicate repository name: {name}")
        if resolved_path in seen_paths:
            raise ValueError(f"Duplicate repository path: {configured_path}")
        seen_names.add(name.casefold())
        seen_paths.add(resolved_path)
        repositories.append(RepositorySpec(name, resolved_path, url))

    if not repositories:
        raise ValueError("repositories.yml does not contain any repositories")

    return sorted(repositories, key=lambda item: item.name.casefold())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=Path("repositories.yml"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        for repository in load_registry(args.registry):
            print(f"{repository.name}\t{repository.path}")
        return 0
    except (FileNotFoundError, ValueError, yaml.YAMLError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
