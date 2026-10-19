#!/usr/bin/env python3
"""Generate a deterministic dependency graph from evaluated MSBuild metadata."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


MSBUILD_PROPERTIES = (
    "PackageId",
    "AssemblyName",
    "TargetFramework",
    "TargetFrameworks",
    "IsPackable",
    "IsTestProject",
)
MSBUILD_ITEMS = ("ProjectReference", "PackageReference")


@dataclass(frozen=True)
class RepositorySpec:
    name: str
    path: Path
    configured_path: str
    url: str


@dataclass(frozen=True)
class EvaluationSpec:
    name: str
    properties: dict[str, str]


def load_registry(path: Path) -> tuple[list[RepositorySpec], list[EvaluationSpec]]:
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

    evaluations: list[EvaluationSpec] = []
    for item in raw.get("evaluations", [{"name": "default", "properties": {}}]):
        name = str(item.get("name", "")).strip()
        properties = {
            str(key): str(value)
            for key, value in (item.get("properties") or {}).items()
        }
        if not name:
            raise ValueError("Every evaluation needs a non-empty name")
        evaluations.append(EvaluationSpec(name, properties))

    return repositories, evaluations


def run_command(command: list[str], cwd: Path | None = None) -> str:
    completed = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"Command failed ({completed.returncode}): {' '.join(command)}\n{detail}")
    return completed.stdout.strip()


def git_value(repository: Path, *arguments: str) -> str:
    try:
        return run_command(["git", "-C", str(repository), *arguments])
    except RuntimeError:
        return ""


def parse_msbuild_json(output: str) -> dict[str, Any]:
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        match = re.search(r'\{\s*"Properties"\s*:', output)
        end = output.rfind("}")
        if match is None or end < match.start():
            raise ValueError(f"MSBuild did not return JSON:\n{output}")
        return json.loads(output[match.start() : end + 1])


def invoke_msbuild(project: Path, properties: dict[str, str]) -> dict[str, Any]:
    command = [
        "dotnet",
        "msbuild",
        str(project),
        "-nologo",
        f"-getProperty:{','.join(MSBUILD_PROPERTIES)}",
        f"-getItem:{','.join(MSBUILD_ITEMS)}",
    ]
    command.extend(f"-p:{key}={value}" for key, value in sorted(properties.items()))
    return parse_msbuild_json(run_command(command, cwd=project.parent))


def discover_projects(repository: RepositorySpec) -> list[Path]:
    if not repository.path.is_dir():
        raise FileNotFoundError(
            f"Repository {repository.name} is missing at {repository.path}"
        )
    projects = [
        path.resolve()
        for path in repository.path.rglob("*.csproj")
        if not any(part in {"bin", "obj", ".git", "graphify-out"} for part in path.parts)
    ]
    return sorted(projects, key=lambda item: item.as_posix().casefold())


def item_metadata(item: dict[str, Any]) -> dict[str, str]:
    return {str(key): str(value) for key, value in item.items() if value not in (None, "")}


def evaluate_project(
    project: Path,
    evaluation: EvaluationSpec,
    evaluator=invoke_msbuild,
) -> list[dict[str, Any]]:
    base = evaluator(project, evaluation.properties)
    properties = base.get("Properties", {})
    target_frameworks = [
        value.strip()
        for value in str(properties.get("TargetFrameworks", "")).split(";")
        if value.strip()
    ]
    if len(target_frameworks) <= 1:
        return [
            {
                "evaluation": evaluation.name,
                "evaluation_properties": dict(evaluation.properties),
                "data": base,
            }
        ]

    results = []
    for target_framework in target_frameworks:
        selected = {**evaluation.properties, "TargetFramework": target_framework}
        results.append(
            {
                "evaluation": evaluation.name,
                "evaluation_properties": selected,
                "data": evaluator(project, selected),
            }
        )
    return results


def project_id(repository_name: str, relative_path: str) -> str:
    return f"project:{repository_name}:{relative_path}"


def package_id(package_name: str) -> str:
    return f"package:{package_name.casefold()}"


def external_project_id(path: str) -> str:
    digest = hashlib.sha256(path.encode("utf-8")).hexdigest()[:16]
    return f"external-project:{digest}"


def bool_property(value: Any) -> bool:
    return str(value).strip().casefold() == "true"


def portable_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def collect_project_records(
    repositories: list[RepositorySpec],
    evaluations: list[EvaluationSpec],
    evaluator=invoke_msbuild,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for repository in repositories:
        for project in discover_projects(repository):
            runs: list[dict[str, Any]] = []
            for evaluation in evaluations:
                runs.extend(evaluate_project(project, evaluation, evaluator=evaluator))
            records.append(
                {
                    "repository": repository,
                    "path": project,
                    "runs": runs,
                }
            )
    return records


def merge_reference(
    references: dict[tuple[str, str], dict[str, Any]],
    key: tuple[str, str],
    base: dict[str, Any],
    evaluation: str,
    target_framework: str,
) -> None:
    if key not in references:
        references[key] = {
            **base,
            "evaluations": [],
            "target_frameworks": [],
            "observations": [],
        }
    reference = references[key]
    if evaluation and evaluation not in reference["evaluations"]:
        reference["evaluations"].append(evaluation)
    if target_framework and target_framework not in reference["target_frameworks"]:
        reference["target_frameworks"].append(target_framework)
    observation = {
        "evaluation": evaluation,
        "target_framework": target_framework,
    }
    for field in ("include", "version"):
        if base.get(field):
            observation[field] = base[field]
    if observation not in reference["observations"]:
        reference["observations"].append(observation)


def build_graph(
    repositories: list[RepositorySpec],
    records: list[dict[str, Any]],
    registry_dir: Path,
) -> dict[str, Any]:
    workspace_root = registry_dir.resolve().parent
    repository_for_path: dict[Path, str] = {}
    projects: list[dict[str, Any]] = []
    raw_by_project_id: dict[str, dict[str, Any]] = {}

    for record in records:
        repository: RepositorySpec = record["repository"]
        path: Path = record["path"].resolve()
        relative_path = path.relative_to(repository.path.resolve()).as_posix()
        identifier = project_id(repository.name, relative_path)
        first_properties = record["runs"][0]["data"].get("Properties", {})
        target_frameworks: set[str] = set()
        for run in record["runs"]:
            props = run["data"].get("Properties", {})
            target_frameworks.update(
                value.strip()
                for value in str(props.get("TargetFrameworks", "")).split(";")
                if value.strip()
            )
            if props.get("TargetFramework"):
                target_frameworks.add(str(props["TargetFramework"]))
        name = str(first_properties.get("AssemblyName") or path.stem)
        package_name = str(first_properties.get("PackageId") or name)
        is_packable = bool_property(first_properties.get("IsPackable"))
        is_test = bool_property(first_properties.get("IsTestProject")) or bool(
            re.search(r"(?:\.Tests|\.E2ETests)$", path.stem, re.IGNORECASE)
        )
        project = {
            "id": identifier,
            "type": "Project",
            "name": name,
            "repository": repository.name,
            "path": relative_path,
            "target_frameworks": sorted(target_frameworks),
            "package_id": package_name if is_packable else None,
            "is_packable": is_packable,
            "is_test": is_test,
        }
        projects.append(project)
        repository_for_path[path] = identifier
        raw_by_project_id[identifier] = record

    project_by_id = {project["id"]: project for project in projects}
    package_owners: dict[str, dict[str, str]] = {}
    for project in projects:
        if project["package_id"]:
            key = project["package_id"].casefold()
            owner = {
                "repository": project["repository"],
                "project": project["id"],
                "package_id": project["package_id"],
            }
            if key in package_owners and package_owners[key]["project"] != project["id"]:
                raise ValueError(f"Package {project['package_id']} has more than one producer")
            package_owners[key] = owner

    project_references: dict[tuple[str, str], dict[str, Any]] = {}
    package_references: dict[tuple[str, str], dict[str, Any]] = {}
    external_projects: dict[str, dict[str, Any]] = {}

    for source_id, record in raw_by_project_id.items():
        project_path: Path = record["path"]
        source_project = project_by_id[source_id]
        for run in record["runs"]:
            data = run["data"]
            props = data.get("Properties", {})
            target_framework = str(props.get("TargetFramework", ""))
            evaluation = str(run.get("evaluation", ""))
            items = data.get("Items", {})

            for item in items.get("ProjectReference", []):
                metadata = item_metadata(item)
                raw_target = metadata.get("FullPath") or metadata.get("Identity", "")
                target_path = Path(raw_target)
                if not target_path.is_absolute():
                    target_path = project_path.parent / target_path
                target_path = target_path.resolve()
                target_id = repository_for_path.get(target_path)
                if target_id is None:
                    portable = portable_path(target_path, workspace_root)
                    target_id = external_project_id(portable)
                    external_projects[target_id] = {
                        "id": target_id,
                        "type": "Project",
                        "name": target_path.stem,
                        "repository": None,
                        "path": portable,
                        "target_frameworks": [],
                        "package_id": None,
                        "is_packable": False,
                        "is_test": False,
                        "unresolved": True,
                    }
                base = {
                    "type": "PROJECT_REFERENCE",
                    "source": source_id,
                    "target": target_id,
                    "include": metadata.get("Identity", ""),
                    "scope": "test" if source_project["is_test"] else "production",
                }
                merge_reference(
                    project_references,
                    (source_id, target_id),
                    base,
                    evaluation,
                    target_framework,
                )

            for item in items.get("PackageReference", []):
                metadata = item_metadata(item)
                package_name = metadata.get("Identity", "").strip()
                if not package_name:
                    continue
                target_id = package_id(package_name)
                version = metadata.get("VersionOverride") or metadata.get("Version", "")
                owner = package_owners.get(package_name.casefold())
                base = {
                    "type": "PACKAGE_REFERENCE",
                    "source": source_id,
                    "target": target_id,
                    "package": package_name,
                    "version": version,
                    "scope": "test" if source_project["is_test"] else "production",
                    "resolved_repository": owner["repository"] if owner else None,
                    "resolved_project": owner["project"] if owner else None,
                }
                merge_reference(
                    package_references,
                    (source_id, package_name.casefold()),
                    base,
                    evaluation,
                    target_framework,
                )

    package_names: dict[str, str] = {
        key: owner["package_id"] for key, owner in package_owners.items()
    }
    for reference in package_references.values():
        package_names.setdefault(reference["package"].casefold(), reference["package"])
    packages = []
    for key, name in sorted(package_names.items()):
        owner = package_owners.get(key)
        packages.append(
            {
                "id": package_id(name),
                "type": "NuGetPackage",
                "name": name,
                "owner_repository": owner["repository"] if owner else None,
                "owner_project": owner["project"] if owner else None,
                "external": owner is None,
            }
        )

    edges: list[dict[str, Any]] = []
    for project in projects:
        edges.append(
            {
                "type": "CONTAINS",
                "source": f"repository:{project['repository']}",
                "target": project["id"],
            }
        )
        if project["package_id"]:
            edges.append(
                {
                    "type": "PUBLISHES",
                    "source": project["id"],
                    "target": package_id(project["package_id"]),
                }
            )
    edges.extend(project_references.values())
    edges.extend(package_references.values())

    dependency_groups: dict[tuple[str, str], dict[str, Any]] = {}

    def add_repository_dependency(
        source_repository: str,
        target_repository: str | None,
        reference_type: str,
        reference: dict[str, Any],
    ) -> None:
        if not target_repository or source_repository == target_repository:
            return
        key = (source_repository, target_repository)
        if key not in dependency_groups:
            dependency_groups[key] = {
                "source": source_repository,
                "target": target_repository,
                "reference_types": [],
                "scopes": [],
                "references": [],
            }
        group = dependency_groups[key]
        if reference_type not in group["reference_types"]:
            group["reference_types"].append(reference_type)
        if reference["scope"] not in group["scopes"]:
            group["scopes"].append(reference["scope"])
        group["references"].append(reference)

    for reference in project_references.values():
        source_repository = project_by_id[reference["source"]]["repository"]
        target = project_by_id.get(reference["target"])
        add_repository_dependency(
            source_repository,
            target["repository"] if target else None,
            "ProjectReference",
            reference,
        )
    for reference in package_references.values():
        source_repository = project_by_id[reference["source"]]["repository"]
        add_repository_dependency(
            source_repository,
            reference.get("resolved_repository"),
            "PackageReference",
            reference,
        )

    repository_nodes = []
    for repository in repositories:
        repository_nodes.append(
            {
                "id": f"repository:{repository.name}",
                "type": "Repository",
                "name": repository.name,
                "path": repository.configured_path,
                "url": repository.url,
                "commit": git_value(repository.path, "rev-parse", "HEAD"),
            }
        )

    evaluation_map: dict[str, dict[str, str]] = {}
    for record in records:
        for run in record["runs"]:
            name = str(run.get("evaluation", "default"))
            properties = {
                str(key): str(value)
                for key, value in run.get("evaluation_properties", {}).items()
            }
            existing = evaluation_map.setdefault(name, properties)
            if existing != properties and "TargetFramework" in properties:
                properties = {key: value for key, value in properties.items() if key != "TargetFramework"}
                existing_without_tfm = {
                    key: value for key, value in existing.items() if key != "TargetFramework"
                }
                if existing_without_tfm == properties:
                    evaluation_map[name] = properties
                    continue
            if existing != properties:
                raise ValueError(f"Evaluation {name} used inconsistent MSBuild properties")

    for collection in (
        project_references.values(),
        package_references.values(),
        dependency_groups.values(),
    ):
        for item in collection:
            for key in ("evaluations", "target_frameworks", "reference_types", "scopes"):
                if key in item:
                    item[key] = sorted(item[key])
            if "references" in item:
                item["references"] = sorted(
                    item["references"],
                    key=lambda ref: (ref["source"], ref["type"], ref["target"]),
                )

    return {
        "schema_version": 1,
        "evaluations": [
            {"name": name, "properties": evaluation_map[name]}
            for name in sorted(evaluation_map, key=str.casefold)
        ],
        "repositories": sorted(repository_nodes, key=lambda item: item["name"].casefold()),
        "projects": sorted(
            [*projects, *external_projects.values()], key=lambda item: item["id"].casefold()
        ),
        "packages": sorted(packages, key=lambda item: item["name"].casefold()),
        "edges": sorted(
            edges,
            key=lambda item: (item["type"], item["source"], item["target"]),
        ),
        "repository_dependencies": sorted(
            dependency_groups.values(),
            key=lambda item: (item["source"].casefold(), item["target"].casefold()),
        ),
    }


def mermaid_text(graph: dict[str, Any]) -> str:
    repositories = graph["repositories"]
    identifiers = {
        repository["name"]: f"R{index}" for index, repository in enumerate(repositories)
    }
    lines = ["flowchart LR"]
    for repository in repositories:
        label = repository["name"].replace('"', "&quot;")
        lines.append(f'    {identifiers[repository["name"]]}["{label}"]')
    for dependency in graph["repository_dependencies"]:
        label = " + ".join(dependency["reference_types"])
        lines.append(
            f'    {identifiers[dependency["source"]]} -->|"{label}"| '
            f'{identifiers[dependency["target"]]}'
        )
    return "\n".join(lines) + "\n"


def dependency_depths(graph: dict[str, Any]) -> dict[str, int]:
    dependencies: dict[str, set[str]] = {
        repository["name"]: set() for repository in graph["repositories"]
    }
    for edge in graph["repository_dependencies"]:
        dependencies[edge["source"]].add(edge["target"])

    memo: dict[str, int] = {}

    def visit(name: str, active: set[str]) -> int:
        if name in memo:
            return memo[name]
        if name in active:
            return 0
        children = dependencies[name]
        depth = 0 if not children else 1 + max(visit(child, active | {name}) for child in children)
        memo[name] = depth
        return depth

    for name in dependencies:
        visit(name, set())
    return memo


def svg_text(graph: dict[str, Any]) -> str:
    depths = dependency_depths(graph)
    columns: dict[int, list[str]] = defaultdict(list)
    for repository in graph["repositories"]:
        columns[depths[repository["name"]]].append(repository["name"])
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
    for dependency in graph["repository_dependencies"]:
        sx, sy = positions[dependency["source"]]
        tx, ty = positions[dependency["target"]]
        x1, y1 = sx + box_width, sy + box_height / 2
        x2, y2 = tx, ty + box_height / 2
        middle = (x1 + x2) / 2
        label = html.escape(" + ".join(dependency["reference_types"]))
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


def write_outputs(graph: dict[str, Any], output_dir: Path, readme: Path | None) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    mermaid = mermaid_text(graph)
    (output_dir / "repositories.json").write_text(
        json.dumps(graph, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (output_dir / "repositories.mmd").write_text(mermaid, encoding="utf-8")
    (output_dir / "repositories.svg").write_text(svg_text(graph), encoding="utf-8")
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
        repositories, evaluations = load_registry(args.registry)
        if args.list_repositories:
            for repository in repositories:
                print(f"{repository.name}\t{repository.path}")
            return 0
        records = collect_project_records(repositories, evaluations)
        graph = build_graph(repositories, records, args.registry.resolve().parent)
        write_outputs(graph, args.output_dir, args.readme)
        unresolved = sum(1 for project in graph["projects"] if project.get("unresolved"))
        print(
            f"Generated {len(graph['repositories'])} repositories, "
            f"{len(graph['projects'])} projects, {len(graph['packages'])} packages, "
            f"and {len(graph['repository_dependencies'])} cross-repository dependencies"
        )
        if unresolved:
            print(f"Warning: {unresolved} ProjectReference target(s) were outside the registry", file=sys.stderr)
        return 0
    except (FileNotFoundError, RuntimeError, ValueError, yaml.YAMLError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
