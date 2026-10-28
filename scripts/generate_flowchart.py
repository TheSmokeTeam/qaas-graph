#!/usr/bin/env python3
"""Generate the QaaS repository flowchart from explicit registry relationships."""

from __future__ import annotations

import argparse
import html
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class RepositorySpec:
    name: str
    path: Path
    configured_path: str
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
        resolved = (registry_path.parent / configured_path).resolve()
        if name.casefold() in seen_names:
            raise ValueError(f"Duplicate repository name: {name}")
        if resolved in seen_paths:
            raise ValueError(f"Duplicate repository path: {configured_path}")
        seen_names.add(name.casefold())
        seen_paths.add(resolved)
        repositories.append(RepositorySpec(name, resolved, configured_path, url))

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


def dependency_depths(
    repositories: list[RepositorySpec], dependencies: list[DependencySpec]
) -> dict[str, int]:
    targets: dict[str, set[str]] = {
        repository.name: set() for repository in repositories
    }
    for dependency in dependencies:
        targets[dependency.source].add(dependency.target)

    memo: dict[str, int] = {}

    def visit(name: str, active: set[str]) -> int:
        if name in memo:
            return memo[name]
        if name in active:
            return 0
        children = targets[name]
        depth = (
            0
            if not children
            else 1 + max(visit(child, active | {name}) for child in children)
        )
        memo[name] = depth
        return depth

    for name in targets:
        visit(name, set())
    return memo


def svg_text(
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
    depths = dependency_depths(repositories, dependencies)
    columns: dict[int, list[str]] = defaultdict(list)
    for repository in repositories:
        columns[depths[repository.name]].append(repository.name)
    for names in columns.values():
        names.sort(key=str.casefold)

    max_depth = max(columns, default=0)
    box_width, box_height = 270, 54
    x_gap, y_gap = 90, 34
    margin = 40
    max_rows = max((len(names) for names in columns.values()), default=1)
    width = margin * 2 + (max_depth + 1) * box_width + max_depth * x_gap
    height = margin * 2 + max_rows * box_height + max(0, max_rows - 1) * y_gap
    positions: dict[str, tuple[float, float]] = {}
    for depth, names in columns.items():
        x = margin + (max_depth - depth) * (box_width + x_gap)
        column_height = len(names) * box_height + max(0, len(names) - 1) * y_gap
        start_y = (height - column_height) / 2
        for index, name in enumerate(names):
            positions[name] = (x, start_y + index * (box_height + y_gap))

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
        '<title id="title">QaaS repository dependencies</title>',
        '<desc id="desc">Arrows point from consuming repositories to the repositories that produce their dependencies.</desc>',
        '<defs><marker id="arrow" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto"><path d="M0,0 L10,3.5 L0,7 Z" fill="#64748b"/></marker></defs>',
        '<style>.node{fill:#eff6ff;stroke:#2563eb;stroke-width:1.5}.label{font:600 14px system-ui,sans-serif;fill:#0f172a}.edge{stroke:#64748b;stroke-width:1.5;fill:none;marker-end:url(#arrow)}.edge-label{font:11px system-ui,sans-serif;fill:#475569}</style>',
    ]
    for dependency in dependencies:
        sx, sy = positions[dependency.source]
        tx, ty = positions[dependency.target]
        x1, y1 = sx + box_width, sy + box_height / 2
        x2, y2 = tx, ty + box_height / 2
        middle = (x1 + x2) / 2
        label = html.escape(dependency.label)
        parts.append(
            f'<path class="edge" d="M{x1:.1f},{y1:.1f} C{middle:.1f},{y1:.1f} {middle:.1f},{y2:.1f} {x2:.1f},{y2:.1f}"/>'
        )
        parts.append(
            f'<text class="edge-label" x="{middle:.1f}" y="{(y1 + y2) / 2 - 5:.1f}" text-anchor="middle">{label}</text>'
        )
    for name, (x, y) in positions.items():
        parts.append(
            f'<rect class="node" x="{x:.1f}" y="{y:.1f}" width="{box_width}" height="{box_height}" rx="8"/>'
        )
        parts.append(
            f'<text class="label" x="{x + box_width / 2:.1f}" y="{y + box_height / 2 + 5:.1f}" text-anchor="middle">{html.escape(name)}</text>'
        )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def update_readme(path: Path, mermaid: str) -> None:
    start = "<!-- repository-graph:start -->"
    end = "<!-- repository-graph:end -->"
    content = path.read_text(encoding="utf-8")
    replacement = f"{start}\n```mermaid\n{mermaid}```\n{end}"
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.DOTALL)
    if not pattern.search(content):
        raise ValueError(f"{path} is missing repository graph markers")
    path.write_text(pattern.sub(replacement, content), encoding="utf-8")


def write_outputs(
    repositories: list[RepositorySpec],
    dependencies: list[DependencySpec],
    output_dir: Path,
    readme: Path | None,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    mermaid = mermaid_text(repositories, dependencies)
    (output_dir / "repositories.mmd").write_text(mermaid, encoding="utf-8")
    (output_dir / "repositories.svg").write_text(
        svg_text(repositories, dependencies), encoding="utf-8"
    )
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
            f"Generated flowchart for {len(repositories)} repositories and "
            f"{len(dependencies)} dependencies"
        )
        return 0
    except (FileNotFoundError, ValueError, yaml.YAMLError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
