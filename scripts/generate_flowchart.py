#!/usr/bin/env python3
"""Generate the QaaS repository flowchart from explicit registry relationships."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class RepositorySpec:
    name: str
    path: Path
    url: str


@dataclass(frozen=True)
class DependencySpec:
    source: str
    target: str
    label: str


def load_registry(path: Path) -> tuple[list[RepositorySpec], list[DependencySpec]]:
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

    canonical_names = {
        repository.name.casefold(): repository.name for repository in repositories
    }
    dependencies: list[DependencySpec] = []
    seen_dependencies: set[tuple[str, str, str]] = set()
    for item in raw.get("dependencies", []):
        source_input = str(item.get("source", "")).strip()
        target_input = str(item.get("target", "")).strip()
        label = str(item.get("label", "")).strip()
        if not source_input or not target_input or not label:
            raise ValueError(
                "Every dependency needs non-empty source, target, and label values"
            )
        for name in (source_input, target_input):
            if name.casefold() not in canonical_names:
                raise ValueError(f"Dependency references unknown repository: {name}")
        source = canonical_names[source_input.casefold()]
        target = canonical_names[target_input.casefold()]
        if source == target:
            raise ValueError(f"Repository cannot depend on itself: {source}")
        key = (source.casefold(), target.casefold(), label.casefold())
        if key in seen_dependencies:
            raise ValueError(f"Duplicate dependency: {source} -> {target} ({label})")
        seen_dependencies.add(key)
        dependencies.append(DependencySpec(source, target, label))

    repositories.sort(key=lambda item: item.name.casefold())
    dependencies.sort(
        key=lambda item: (
            item.source.casefold(),
            item.target.casefold(),
            item.label.casefold(),
        )
    )
    return repositories, dependencies


def mermaid_text(
    repositories: list[RepositorySpec], dependencies: list[DependencySpec]
) -> str:
    repositories = sorted(repositories, key=lambda item: item.name.casefold())
    dependencies = sorted(
        dependencies,
        key=lambda item: (
            item.source.casefold(),
            item.target.casefold(),
            item.label.casefold(),
        ),
    )
    identifiers = {
        repository.name: f"R{index}" for index, repository in enumerate(repositories)
    }
    lines = ["flowchart LR"]
    for repository in repositories:
        label = repository.name.replace('"', "&quot;")
        lines.append(f'    {identifiers[repository.name]}["{label}"]')
    for dependency in dependencies:
        label = dependency.label.replace('"', "&quot;")
        lines.append(
            f'    {identifiers[dependency.source]} -->|"{label}"| '
            f'{identifiers[dependency.target]}'
        )
    return "\n".join(lines) + "\n"


def update_readme(path: Path, mermaid: str) -> None:
    start = "<!-- repository-graph:start -->"
    end = "<!-- repository-graph:end -->"
    content = path.read_text(encoding="utf-8")
    replacement = f"{start}\n```mermaid\n{mermaid}```\n{end}"
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.DOTALL)
    if not pattern.search(content):
        raise ValueError(f"{path} is missing repository graph markers")
    path.write_text(pattern.sub(lambda _: replacement, content), encoding="utf-8")


def write_outputs(
    repositories: list[RepositorySpec],
    dependencies: list[DependencySpec],
    output_dir: Path,
    readme: Path | None,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    mermaid = mermaid_text(repositories, dependencies)
    (output_dir / "repositories.mmd").write_text(mermaid, encoding="utf-8")
    if readme:
        update_readme(readme, mermaid)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=Path("repositories.yml"))
    parser.add_argument("--output-dir", type=Path, default=Path("graphs"))
    parser.add_argument("--readme", type=Path)
    parser.add_argument(
        "--list-repositories",
        action="store_true",
        help="Print name and absolute path as tab-separated rows, then exit",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        repositories, dependencies = load_registry(args.registry)
        if args.list_repositories:
            for repository in repositories:
                print(f"{repository.name}\t{repository.path}")
            return 0
        write_outputs(repositories, dependencies, args.output_dir, args.readme)
        print(
            f"Generated Mermaid flowchart for {len(repositories)} repositories and "
            f"{len(dependencies)} dependencies"
        )
        return 0
    except (FileNotFoundError, ValueError, yaml.YAMLError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
