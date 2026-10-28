from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
GENERATE = ROOT / "scripts" / "generate.sh"
UPDATE_WORKFLOW = ROOT / ".github" / "workflows" / "update-graphs.yml"
DEPLOY_WORKFLOW = ROOT / ".github" / "workflows" / "deploy-pages.yml"


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

    def test_generation_uses_the_repository_registry_for_graphify(self) -> None:
        generator = GENERATE.read_text(encoding="utf-8")

        self.assertIn("list_repositories.py", generator)
        self.assertIn("graphify extract", generator)
        self.assertIn("graphify merge-graphs", generator)

    def test_deploy_runs_after_generated_graph_artifacts_reach_main(self) -> None:
        workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("branches: [main]", workflow)
        self.assertIn("- graphify-out/**", workflow)


if __name__ == "__main__":
    unittest.main()
