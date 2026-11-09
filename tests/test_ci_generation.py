from __future__ import annotations

import os
import subprocess
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
GENERATE = ROOT / "scripts" / "generate.sh"
UPDATE_WORKFLOW = ROOT / ".github" / "workflows" / "update-graphs.yml"
DEPLOY_WORKFLOW = ROOT / ".github" / "workflows" / "deploy-pages.yml"
PYPROJECT = ROOT / "pyproject.toml"
LOCKFILE = ROOT / "uv.lock"
REQUIREMENTS = ROOT / "requirements.txt"


class CIGenerationBoundaryTests(unittest.TestCase):
    def run_generate(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment.pop("GITHUB_ACTIONS", None)
        environment.pop("QAAS_GRAPH_CI", None)
        return subprocess.run(
            ["bash", str(GENERATE), *arguments],
            cwd=ROOT,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_generation_is_rejected_outside_github_actions(self) -> None:
        completed = self.run_generate()

        self.assertEqual(completed.returncode, 1)
        self.assertIn("Graphify generation is CI-only", completed.stderr)
        self.assertIn("actions/workflows/update-graphs.yml", completed.stderr)

    def test_help_remains_available_without_generating(self) -> None:
        completed = self.run_generate("--help")

        self.assertEqual(completed.returncode, 0)
        self.assertIn("Usage:", completed.stdout)
        self.assertNotIn("CI-only", completed.stderr)

    def test_update_workflow_uses_the_guarded_generator(self) -> None:
        workflow = UPDATE_WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("run: scripts/generate.sh", workflow)
        self.assertIn('QAAS_GRAPH_CI: "true"', workflow)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("repository_dispatch:", workflow)
        self.assertIn("types: [qaas-package-mirror-synced]", workflow)
        self.assertNotIn("schedule:", workflow)

    def test_update_workflow_checks_only_open_pull_requests_for_automation_branch(
        self,
    ) -> None:
        workflow = UPDATE_WORKFLOW.read_text(encoding="utf-8")

        self.assertIn('gh pr list --state open --head "$branch"', workflow)
        self.assertNotIn('gh pr view "$branch"', workflow)

    def test_python_tooling_is_an_exact_locked_non_package_project(self) -> None:
        self.assertFalse(REQUIREMENTS.exists())
        project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))

        self.assertEqual(project["project"]["requires-python"], ">=3.12")
        self.assertEqual(
            project["project"]["dependencies"],
            ["graphifyy==0.9.90", "PyYAML==6.0.3"],
        )
        self.assertIs(project["tool"]["uv"]["package"], False)
        self.assertEqual(project["tool"]["uv"]["required-version"], "==0.13.7")

        lock = LOCKFILE.read_text(encoding="utf-8")
        self.assertIn('name = "graphifyy"', lock)
        self.assertIn('version = "0.9.90"', lock)
        self.assertIn('name = "pyyaml"', lock.lower())
        self.assertIn('version = "6.0.3"', lock)

    def test_update_workflow_uses_pinned_uv_and_the_committed_lock(self) -> None:
        workflow = UPDATE_WORKFLOW.read_text(encoding="utf-8")

        self.assertIn(
            "astral-sh/setup-uv@68559e99cc807c1eb7e9238fd43a15bba460eec2",
            workflow,
        )
        self.assertIn('version: "0.13.7"', workflow)
        self.assertIn(
            "uv run --locked python -m unittest discover -s tests -v", workflow
        )
        self.assertNotIn("pip install", workflow)
        self.assertNotIn("requirements.txt", workflow)

    def test_generation_uses_the_repository_registry_for_graphify(self) -> None:
        generator = GENERATE.read_text(encoding="utf-8")

        self.assertIn("list_repositories.py", generator)
        locked_prefix = 'uv run --project "$repo_dir" --locked'
        self.assertIn(
            f'{locked_prefix} python scripts/list_repositories.py', generator
        )
        self.assertIn(f"{locked_prefix} graphify extract", generator)
        self.assertIn(f"{locked_prefix} graphify merge-graphs", generator)
        self.assertIn(f"{locked_prefix} graphify export html", generator)
        self.assertNotIn("command -v graphify", generator)

    def test_deploy_runs_after_generated_graph_artifacts_reach_main(self) -> None:
        workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("branches: [main]", workflow)
        self.assertIn("- graphify-out/**", workflow)


if __name__ == "__main__":
    unittest.main()
