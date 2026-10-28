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

    def test_registry_paths_are_relative_and_dependencies_are_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "consumer").mkdir()
            (root / "producer").mkdir()
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

            self.assertEqual(repositories[0].name, "Consumer")
            self.assertEqual(repositories[0].path, (root / "consumer").resolve())
            self.assertEqual(dependencies[0].source, "Consumer")
            self.assertEqual(dependencies[0].target, "Producer")
            self.assertEqual(dependencies[0].label, "PackageReference")

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
            MODULE.RepositorySpec("Producer", Path("/producer"), "producer", ""),
            MODULE.RepositorySpec("Consumer", Path("/consumer"), "consumer", ""),
        ]
        dependencies = [
            MODULE.DependencySpec("Consumer", "Producer", "PackageReference")
        ]

        output = MODULE.mermaid_text(repositories, dependencies)

        self.assertIn('R0["Consumer"]', output)
        self.assertIn('R1["Producer"]', output)
        self.assertIn('R0 -->|"PackageReference"| R1', output)

    def test_outputs_include_only_flowchart_artifacts(self) -> None:
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
                MODULE.RepositorySpec("Producer", root / "producer", "producer", ""),
                MODULE.RepositorySpec("Consumer", root / "consumer", "consumer", ""),
            ]
            dependencies = [
                MODULE.DependencySpec("Consumer", "Producer", "depends on")
            ]

            MODULE.write_outputs(repositories, dependencies, output_dir, readme)

            self.assertEqual(
                {path.name for path in output_dir.iterdir()},
                {"repositories.mmd", "repositories.svg"},
            )
            self.assertIn("flowchart LR", readme.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
