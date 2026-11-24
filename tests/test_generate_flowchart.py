from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "generate_flowchart.py"
SPEC = importlib.util.spec_from_file_location("generate_flowchart", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class FlowchartTests(unittest.TestCase):
    def write_registry(self, root: Path, content: str) -> Path:
        registry = root / "repositories.yml"
        registry.write_text(content, encoding="utf-8")
        return registry

    def test_registry_paths_are_relative_and_sorted_with_explicit_dependencies(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = self.write_registry(
                root,
                """version: 1
repositories:
  - name: Producer
    path: producer
    url: https://example.invalid/Producer.git
  - name: Consumer
    path: consumer
    url: https://example.invalid/Consumer.git
dependencies:
  - source: Consumer
    target: Producer
    label: PackageReference
""",
            )

            repositories, dependencies = MODULE.load_registry(registry)

            self.assertEqual(
                [item.name for item in repositories], ["Consumer", "Producer"]
            )
            self.assertEqual(repositories[0].path, (root / "consumer").resolve())
            self.assertEqual(dependencies[0].source, "Consumer")
            self.assertEqual(dependencies[0].target, "Producer")
            self.assertEqual(dependencies[0].label, "PackageReference")

    def test_registry_requires_supported_version_and_repositories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for content, message in (
                ("version: 2\nrepositories: []\n", "must declare version: 1"),
                ("version: 1\nrepositories: []\n", "does not contain any repositories"),
            ):
                with self.subTest(message=message):
                    registry = self.write_registry(root, content)
                    with self.assertRaisesRegex(ValueError, message):
                        MODULE.load_registry(registry)

    def test_duplicate_repository_names_are_rejected_case_insensitively(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry = self.write_registry(
                Path(directory),
                """version: 1
repositories:
  - name: Example
    path: first
  - name: example
    path: second
""",
            )

            with self.assertRaisesRegex(ValueError, "Duplicate repository name"):
                MODULE.load_registry(registry)

    def test_dependency_must_reference_registered_repositories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry = self.write_registry(
                Path(directory),
                """version: 1
repositories:
  - name: Consumer
    path: consumer
dependencies:
  - source: Consumer
    target: Missing
    label: depends on
""",
            )

            with self.assertRaisesRegex(ValueError, "unknown repository: Missing"):
                MODULE.load_registry(registry)

    def test_mermaid_is_deterministic_and_points_consumer_to_dependency(self) -> None:
        repositories = [
            MODULE.RepositorySpec("Producer", Path("/producer"), ""),
            MODULE.RepositorySpec("Consumer", Path("/consumer"), ""),
        ]
        dependencies = [
            MODULE.DependencySpec("Consumer", "Producer", "PackageReference")
        ]

        output = MODULE.mermaid_text(repositories, dependencies)

        self.assertEqual(
            output,
            'flowchart LR\n    R0["Consumer"]\n    R1["Producer"]\n'
            '    R0 -->|"PackageReference"| R1\n',
        )

    def test_output_is_only_mermaid_and_readme_embeds_it_exactly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output_dir = root / "graphs"
            readme = root / "README.md"
            readme.write_text(
                "before\n<!-- repository-graph:start -->\nold\n"
                "<!-- repository-graph:end -->\nafter\n",
                encoding="utf-8",
            )
            repositories = [
                MODULE.RepositorySpec("Producer", root / "producer", ""),
                MODULE.RepositorySpec("Consumer", root / "consumer", ""),
            ]
            dependencies = [
                MODULE.DependencySpec("Consumer", "Producer", "depends on")
            ]

            MODULE.write_outputs(repositories, dependencies, output_dir, readme)

            self.assertEqual(
                {path.name for path in output_dir.iterdir()}, {"repositories.mmd"}
            )
            mermaid = (output_dir / "repositories.mmd").read_text(encoding="utf-8")
            self.assertEqual(
                readme.read_text(encoding="utf-8"),
                "before\n<!-- repository-graph:start -->\n"
                f"```mermaid\n{mermaid}```\n"
                "<!-- repository-graph:end -->\nafter\n",
            )


if __name__ == "__main__":
    unittest.main()
