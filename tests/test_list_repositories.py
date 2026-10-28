from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "list_repositories.py"
SPEC = importlib.util.spec_from_file_location("list_repositories", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class RepositoryRegistryTests(unittest.TestCase):
    def test_registry_paths_are_relative_and_sorted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = root / "repositories.yml"
            registry.write_text(
                """version: 1
repositories:
  - name: Producer
    path: producer
    url: https://example.invalid/Producer.git
  - name: Consumer
    path: consumer
    url: https://example.invalid/Consumer.git
""",
                encoding="utf-8",
            )

            repositories = MODULE.load_registry(registry)

            self.assertEqual(
                [item.name for item in repositories], ["Consumer", "Producer"]
            )
            self.assertEqual(repositories[0].path, (root / "consumer").resolve())

    def test_duplicate_repository_names_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = root / "repositories.yml"
            registry.write_text(
                """version: 1
repositories:
  - name: Example
    path: first
  - name: example
    path: second
""",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "Duplicate repository name"):
                MODULE.load_registry(registry)


if __name__ == "__main__":
    unittest.main()
