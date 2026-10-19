from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "extract_dependencies.py"
SPEC = importlib.util.spec_from_file_location("extract_dependencies", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def msbuild_data(
    *,
    name: str,
    package_id: str,
    packable: bool = True,
    project_references: list[dict[str, str]] | None = None,
    package_references: list[dict[str, str]] | None = None,
) -> dict:
    return {
        "Properties": {
            "AssemblyName": name,
            "PackageId": package_id,
            "TargetFramework": "net10.0",
            "TargetFrameworks": "",
            "IsPackable": str(packable).lower(),
            "IsTestProject": "false",
        },
        "Items": {
            "ProjectReference": project_references or [],
            "PackageReference": package_references or [],
        },
    }


class DependencyGraphTests(unittest.TestCase):
    def test_registry_paths_are_relative_to_registry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "source").mkdir()
            registry = root / "repositories.yml"
            registry.write_text(
                """version: 1
repositories:
  - name: Example
    path: source
    url: https://example.invalid/Example.git
evaluations:
  - name: local
    properties:
      UseLocalReferences: true
""",
                encoding="utf-8",
            )
            repositories, evaluations = MODULE.load_registry(registry)

            self.assertEqual(repositories[0].path, (root / "source").resolve())
            self.assertEqual(evaluations[0].properties, {"UseLocalReferences": "True"})

    def test_package_and_project_references_resolve_to_same_repository(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            producer_path = root / "Producer"
            consumer_path = root / "Consumer"
            producer_project = producer_path / "src" / "Producer.csproj"
            consumer_project = consumer_path / "src" / "Consumer.csproj"
            producer_project.parent.mkdir(parents=True)
            consumer_project.parent.mkdir(parents=True)
            producer_project.touch()
            consumer_project.touch()
            producer = MODULE.RepositorySpec("Producer", producer_path, "../Producer", "")
            consumer = MODULE.RepositorySpec("Consumer", consumer_path, "../Consumer", "")
            records = [
                {
                    "repository": producer,
                    "path": producer_project,
                    "runs": [
                        {
                            "evaluation": "default",
                            "data": msbuild_data(name="Producer", package_id="Company.Producer"),
                        }
                    ],
                },
                {
                    "repository": consumer,
                    "path": consumer_project,
                    "runs": [
                        {
                            "evaluation": "nuget",
                            "data": msbuild_data(
                                name="Consumer",
                                package_id="Company.Consumer",
                                package_references=[
                                    {"Identity": "Company.Producer", "Version": "2.0.0"}
                                ],
                            ),
                        },
                        {
                            "evaluation": "local",
                            "data": msbuild_data(
                                name="Consumer",
                                package_id="Company.Consumer",
                                project_references=[
                                    {
                                        "Identity": "../../Producer/src/Producer.csproj",
                                        "FullPath": str(producer_project),
                                    }
                                ],
                            ),
                        },
                    ],
                },
            ]

            graph = MODULE.build_graph([producer, consumer], records, root / "qaas-graph")

            self.assertEqual(len(graph["repository_dependencies"]), 1)
            dependency = graph["repository_dependencies"][0]
            self.assertEqual(dependency["source"], "Consumer")
            self.assertEqual(dependency["target"], "Producer")
            self.assertEqual(
                dependency["reference_types"], ["PackageReference", "ProjectReference"]
            )
            package_reference = next(
                reference
                for reference in dependency["references"]
                if reference["type"] == "PACKAGE_REFERENCE"
            )
            self.assertEqual(
                package_reference["observations"],
                [
                    {
                        "evaluation": "nuget",
                        "target_framework": "net10.0",
                        "version": "2.0.0",
                    }
                ],
            )

    def test_mermaid_is_deterministic_and_points_consumer_to_dependency(self) -> None:
        graph = {
            "repositories": [
                {"name": "Consumer"},
                {"name": "Producer"},
            ],
            "repository_dependencies": [
                {
                    "source": "Consumer",
                    "target": "Producer",
                    "reference_types": ["PackageReference"],
                }
            ],
        }

        output = MODULE.mermaid_text(graph)

        self.assertIn('R0["Consumer"]', output)
        self.assertIn('R1["Producer"]', output)
        self.assertIn('R0 -->|"PackageReference"| R1', output)


if __name__ == "__main__":
    unittest.main()
