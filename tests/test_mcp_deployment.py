from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).parents[1]
DOCKERFILE = ROOT / "Dockerfile.mcp"
MCP_REQUIREMENTS = ROOT / "requirements-mcp.txt"
DEPLOY_WORKFLOW = ROOT / ".github" / "workflows" / "deploy-mcp.yml"
SMOKE_SCRIPT = ROOT / "scripts" / "smoke_mcp.py"

SPEC = importlib.util.spec_from_file_location("smoke_mcp", SMOKE_SCRIPT)
assert SPEC and SPEC.loader
SMOKE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SMOKE)


class MCPDeploymentTests(unittest.TestCase):
    def test_runtime_uses_pinned_graphify_mcp_extra_as_non_root(self) -> None:
        requirements = MCP_REQUIREMENTS.read_text(encoding="utf-8")
        dockerfile = DOCKERFILE.read_text(encoding="utf-8")

        self.assertEqual(requirements.strip(), "graphifyy[mcp]==0.9.90")
        self.assertIn("FROM python:3.12-slim", dockerfile)
        self.assertIn("USER graphify", dockerfile)
        self.assertIn("COPY --chown=graphify:graphify graphify-out/graph.json", dockerfile)
        self.assertIn("GRAPHIFY_API_KEY is required", dockerfile)
        self.assertIn("--transport http", dockerfile)
        self.assertIn("--path /mcp", dockerfile)
        self.assertIn("--stateless", dockerfile)
        self.assertNotIn("generate.sh", dockerfile)

    def test_deployment_tracks_graph_updates_and_uses_wif(self) -> None:
        workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("branches: [main]", workflow)
        self.assertIn("- graphify-out/graph.json", workflow)
        self.assertIn("google-github-actions/auth@v3", workflow)
        self.assertIn("workload_identity_provider:", workflow)
        self.assertNotIn("credentials_json:", workflow)
        self.assertIn("docker push \"$IMAGE_URI\"", workflow)

    def test_cloud_run_contract_is_warm_key_protected_and_rollback_safe(self) -> None:
        workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("GRAPHIFY_API_KEY=qaas-graph-mcp-api-key:latest", workflow)
        self.assertIn("--memory=512Mi", workflow)
        self.assertIn("--min=1", workflow)
        self.assertIn("--max=3", workflow)
        self.assertIn("--concurrency=20", workflow)
        self.assertIn("--timeout=300", workflow)
        self.assertIn("--allow-unauthenticated", workflow)
        self.assertIn("Restore previous revision after failed smoke test", workflow)
        self.assertIn("--to-revisions \"${PREVIOUS_REVISION}=100\"", workflow)

    def test_smoke_parser_accepts_graphify_sse_response(self) -> None:
        response = SMOKE.parse_mcp_response(
            b'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n\n',
            "text/event-stream",
        )

        self.assertEqual(response["id"], 1)
        self.assertTrue(response["result"]["ok"])

    def test_smoke_contract_selects_a_real_unique_edge(self) -> None:
        nodes, edges, source, target = SMOKE.load_graph_contract(
            ROOT / "graphify-out" / "graph.json"
        )

        self.assertGreater(nodes, 0)
        self.assertGreater(edges, 0)
        self.assertNotEqual(source, target)


if __name__ == "__main__":
    unittest.main()
