#!/usr/bin/env python3
"""End-to-end smoke test for the read-only QaaS Graph MCP service."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import math
import os
from pathlib import Path
import re
import secrets
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PROTOCOL_VERSION = "2025-03-26"
EXPECTED_GRAPH_TOOLS = {
    "query_graph",
    "get_node",
    "get_neighbors",
    "get_community",
    "god_nodes",
    "graph_stats",
    "shortest_path",
}


class SmokeFailure(RuntimeError):
    """Raised when the deployed MCP service violates its query contract."""


def parse_mcp_response(body: bytes, content_type: str) -> dict[str, Any]:
    """Parse either a JSON or an SSE Streamable HTTP response."""
    text = body.decode("utf-8")
    if "text/event-stream" not in content_type.lower():
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise SmokeFailure("MCP response was not a JSON object")
        return parsed

    events: list[str] = []
    data_lines: list[str] = []
    for raw_line in text.splitlines():
        if not raw_line:
            if data_lines:
                events.append("\n".join(data_lines))
                data_lines = []
            continue
        if raw_line.startswith("data:"):
            data_lines.append(raw_line[5:].lstrip())
    if data_lines:
        events.append("\n".join(data_lines))

    for event in reversed(events):
        parsed = json.loads(event)
        if isinstance(parsed, dict) and parsed.get("jsonrpc") == "2.0":
            return parsed
    raise SmokeFailure("SSE response contained no JSON-RPC message")


class McpClient:
    def __init__(self, url: str, token: str | None, timeout: float) -> None:
        self.url = url
        self.token = token
        self.timeout = timeout
        self._next_id = 1

    def post(self, payload: dict[str, Any]) -> tuple[int, str, bytes]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if self.token is not None:
            headers["Authorization"] = f"Bearer {self.token}"
        request = Request(
            self.url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return (
                    response.status,
                    response.headers.get("Content-Type", ""),
                    response.read(),
                )
        except HTTPError as error:
            return (
                error.code,
                error.headers.get("Content-Type", ""),
                error.read(),
            )

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        status, content_type, body = self.post(payload)
        if status != 200:
            raise SmokeFailure(
                f"{method} returned HTTP {status}: {body.decode('utf-8', errors='replace')}"
            )
        response = parse_mcp_response(body, content_type)
        if response.get("id") != request_id:
            raise SmokeFailure(
                f"{method} returned response id {response.get('id')!r}, expected {request_id}"
            )
        if "error" in response:
            raise SmokeFailure(f"{method} returned JSON-RPC error: {response['error']}")
        result = response.get("result")
        if not isinstance(result, dict):
            raise SmokeFailure(f"{method} returned no result object")
        return result

    def initialize(self) -> dict[str, Any]:
        return self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "qaas-graph-smoke", "version": "1.0"},
            },
        )

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        result = self.request(
            "tools/call", {"name": name, "arguments": arguments}
        )
        if result.get("isError"):
            raise SmokeFailure(f"{name} returned a tool error: {result}")
        blocks = result.get("content")
        if not isinstance(blocks, list):
            raise SmokeFailure(f"{name} returned no content blocks")
        texts = [
            block.get("text", "")
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        text = "\n".join(part for part in texts if part)
        if not text:
            raise SmokeFailure(f"{name} returned no text content")
        return text


def initialize_when_ready(
    client: McpClient, startup_timeout: float
) -> dict[str, Any]:
    deadline = time.monotonic() + startup_timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return client.initialize()
        except (OSError, URLError, SmokeFailure) as error:
            last_error = error
            time.sleep(0.5)
    raise SmokeFailure(
        f"MCP server was not ready within {startup_timeout:g}s: {last_error}"
    )


def expect_unauthorized(url: str, token: str | None, timeout: float) -> None:
    client = McpClient(url, token, timeout)
    status, _, body = client.post(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "qaas-graph-smoke", "version": "1.0"},
            },
        }
    )
    if status != 401:
        raise SmokeFailure(
            f"Expected HTTP 401 for invalid authentication, got {status}: "
            f"{body.decode('utf-8', errors='replace')}"
        )


def load_graph_contract(
    graph_path: Path,
) -> tuple[int, int, str, str]:
    data = json.loads(graph_path.read_text(encoding="utf-8"))
    nodes = data.get("nodes")
    links = data.get("links", data.get("edges"))
    if not isinstance(nodes, list) or not isinstance(links, list):
        raise SmokeFailure(f"Invalid node-link graph in {graph_path}")

    labels_by_id = {
        node.get("id"): node.get("label")
        for node in nodes
        if isinstance(node, dict)
        and isinstance(node.get("id"), str)
        and isinstance(node.get("label"), str)
    }
    frequencies = Counter(labels_by_id.values())

    def usable(label: str | None) -> bool:
        return bool(
            label
            and 3 <= len(label) <= 80
            and re.match(r"^[A-Za-z]", label)
            and frequencies[label] == 1
        )

    for link in links:
        if not isinstance(link, dict):
            continue
        source_label = labels_by_id.get(link.get("source"))
        target_label = labels_by_id.get(link.get("target"))
        if (
            source_label != target_label
            and usable(source_label)
            and usable(target_label)
        ):
            return len(nodes), len(links), source_label, target_label
    raise SmokeFailure("Graph has no uniquely labelled edge suitable for smoke tests")


def assert_graph_stats(text: str, expected_nodes: int, expected_edges: int) -> None:
    nodes_match = re.search(r"^Nodes: (\d+)$", text, re.MULTILINE)
    edges_match = re.search(r"^Edges: (\d+)$", text, re.MULTILINE)
    if not nodes_match or not edges_match:
        raise SmokeFailure(f"graph_stats returned an unexpected response: {text}")
    actual = (int(nodes_match.group(1)), int(edges_match.group(1)))
    expected = (expected_nodes, expected_edges)
    if actual != expected:
        raise SmokeFailure(
            f"Remote graph counts {actual} do not match packaged graph counts {expected}"
        )


def percentile_95(samples: list[float]) -> float:
    ordered = sorted(samples)
    index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return ordered[index]


def run(args: argparse.Namespace) -> None:
    token = os.environ.get(args.token_env, "")
    if not token:
        raise SmokeFailure(f"Environment variable {args.token_env} is not set")

    expected_nodes, expected_edges, source_label, target_label = load_graph_contract(
        args.graph
    )
    client = McpClient(args.url, token, args.request_timeout)
    initialized = initialize_when_ready(client, args.startup_timeout)
    server_info = initialized.get("serverInfo", {})
    if server_info.get("name") != "graphify":
        raise SmokeFailure(f"Unexpected MCP server identity: {server_info}")

    expect_unauthorized(args.url, None, args.request_timeout)
    expect_unauthorized(
        args.url, f"invalid-{secrets.token_urlsafe(16)}", args.request_timeout
    )

    tools_result = client.request("tools/list", {})
    tools = tools_result.get("tools")
    if not isinstance(tools, list):
        raise SmokeFailure("tools/list returned no tools array")
    tool_names = {
        tool.get("name") for tool in tools if isinstance(tool, dict)
    }
    missing_tools = EXPECTED_GRAPH_TOOLS - tool_names
    if missing_tools:
        raise SmokeFailure(f"Missing graph tools: {sorted(missing_tools)}")

    stats = client.call_tool("graph_stats", {})
    assert_graph_stats(stats, expected_nodes, expected_edges)

    node = client.call_tool("get_node", {"label": source_label})
    if "Node:" not in node or "Source:" not in node:
        raise SmokeFailure(f"get_node returned an unexpected response: {node}")

    neighbors = client.call_tool(
        "get_neighbors", {"label": source_label, "token_budget": 512}
    )
    if "Neighbors of" not in neighbors:
        raise SmokeFailure(
            f"get_neighbors returned an unexpected response: {neighbors}"
        )

    path = client.call_tool(
        "shortest_path",
        {
            "source": source_label,
            "target": target_label,
            "max_hops": 2,
            "undirected": True,
        },
    )
    if not re.search(r"Shortest path \(1 hop", path):
        raise SmokeFailure(f"shortest_path did not traverse the selected edge: {path}")

    query_arguments = {
        "question": source_label,
        "mode": "bfs",
        "depth": 1,
        "token_budget": 512,
    }
    query = client.call_tool("query_graph", query_arguments)
    if "NODE " not in query or "[src=" not in query:
        raise SmokeFailure(f"query_graph omitted source evidence: {query}")

    samples: list[float] = []
    for _ in range(args.latency_samples):
        started = time.perf_counter()
        client.call_tool("query_graph", query_arguments)
        samples.append(time.perf_counter() - started)
    p95 = percentile_95(samples)
    if p95 > args.max_p95_seconds:
        raise SmokeFailure(
            f"Warm query p95 {p95:.3f}s exceeded {args.max_p95_seconds:.3f}s"
        )

    extra_tools = sorted(name for name in tool_names - EXPECTED_GRAPH_TOOLS if name)
    print(
        f"MCP smoke test passed: {expected_nodes} nodes, {expected_edges} edges, "
        f"{len(EXPECTED_GRAPH_TOOLS)} graph tools, warm query p95={p95:.3f}s"
    )
    if extra_tools:
        print(f"Additional tools to disable client-side: {', '.join(extra_tools)}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Smoke-test a Graphify Streamable HTTP MCP endpoint"
    )
    parser.add_argument("--url", required=True, help="Full MCP URL ending in /mcp")
    parser.add_argument(
        "--graph",
        required=True,
        type=Path,
        help="Local graph.json that the remote service must match",
    )
    parser.add_argument(
        "--token-env",
        default="GRAPHIFY_API_KEY",
        help="Environment variable containing the bearer token",
    )
    parser.add_argument("--startup-timeout", type=float, default=30.0)
    parser.add_argument("--request-timeout", type=float, default=15.0)
    parser.add_argument("--latency-samples", type=int, default=10)
    parser.add_argument("--max-p95-seconds", type=float, default=2.0)
    args = parser.parse_args()
    if args.latency_samples < 1:
        parser.error("--latency-samples must be at least 1")
    if args.max_p95_seconds <= 0:
        parser.error("--max-p95-seconds must be positive")
    return args


if __name__ == "__main__":
    try:
        run(parse_args())
    except (SmokeFailure, OSError, URLError, json.JSONDecodeError) as error:
        print(f"MCP smoke test failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
