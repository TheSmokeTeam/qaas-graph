# QaaS ecosystem graph

This repository is the reproducible architecture map for the QaaS repositories.
It keeps two complementary views:

**[Open the interactive QaaS ecosystem graph](https://thesmoketeam.github.io/qaas-graph/)**

- `graphs/repositories.json` is the evaluated MSBuild dependency model. It records repositories, projects, published package identities, `ProjectReference` edges, `PackageReference` edges, versions, target frameworks, evaluation modes, and source commit SHAs.
- `graphify-out/graph.json` and `graphify-out/graph.html` are the merged code-level Graphify graph and interactive visualization.

Arrows point from the consuming repository to the repository that produces its dependency.

<!-- repository-graph:start -->
```mermaid
flowchart LR
    R0["QaaS.Common.Assertions"]
    R1["QaaS.Common.Generators"]
    R2["QaaS.Common.Probes"]
    R3["QaaS.Common.Processors"]
    R4["QaaS.Framework"]
    R5["QaaS.Mocker"]
    R6["QaaS.Mocker.CommunicationObjects"]
    R7["QaaS.Runner"]
    R0 -->|"PackageReference"| R4
    R1 -->|"PackageReference"| R4
    R2 -->|"PackageReference"| R4
    R3 -->|"PackageReference"| R4
    R5 -->|"PackageReference"| R4
    R5 -->|"PackageReference"| R6
    R6 -->|"PackageReference"| R4
    R7 -->|"PackageReference"| R4
    R7 -->|"PackageReference"| R6
```
<!-- repository-graph:end -->

## Update the graphs

Graph creation is CI-only. Do not clone the QaaS source repositories or regenerate
graph artifacts on a workstation.

To request an update, run the
[Update ecosystem graphs workflow](https://github.com/TheSmokeTeam/qaas-graph/actions/workflows/update-graphs.yml)
from the GitHub Actions page. The same workflow accepts the
`qaas-package-mirror-synced` repository-dispatch event emitted after a successful
full `QaaS.PackageMirror` sync. It has no scheduled run.

The workflow checks out every source repository, runs the tests, creates both graph
views, and opens or refreshes the `automation/update-graphs` pull request when the
committed artifacts changed. Review and merge that pull request entirely on GitHub.
`scripts/generate.sh` intentionally refuses to run outside GitHub Actions so the CI
environment remains the single graph producer.

### Automation flow

1. A full PackageMirror sync is started with `workflow_dispatch`.
2. If the sync fails, the chain stops. If it succeeds, PackageMirror dispatches
   the `qaas-package-mirror-synced` event to this repository.
3. The `Update ecosystem graphs` workflow checks out the registered source
   repositories, runs the tests, and regenerates the graph artifacts.
4. If the artifacts are unchanged, the workflow finishes without a pull request.
5. If they changed, the workflow opens or updates the
   `automation/update-graphs` pull request.
6. Merging that pull request into `main` starts the `Deploy interactive graph`
   workflow, which publishes the interactive graph to GitHub Pages.

### How artifact changes are detected

An artifact in this repository is a file generated from the checked-out QaaS
source repositories rather than written manually. The committed graph artifacts
are `graphs/repositories.json`, `graphs/repositories.mmd`,
`graphs/repositories.svg`, `graphify-out/graph.json`, and
`graphify-out/graph.html`. The generated Mermaid block in `README.md` is checked
at the same time.

After regeneration, the workflow runs:

```bash
git diff --quiet -- README.md graphs graphify-out
```

Git compares the regenerated contents with the versions in the commit that the
workflow checked out. File timestamps do not matter. If all tracked contents are
identical, the command succeeds and the workflow exits without creating a pull
request. If any content differs, the command reports a difference and CI commits
those paths to `automation/update-graphs`, then opens or updates its pull request.

This use of *artifact* is distinct from a temporary GitHub Actions artifact. The
graph files above are committed repository outputs. During deployment,
`actions/upload-pages-artifact` separately packages `graphify-out/graph.html` for
GitHub Pages.

Two PackageMirror modes intentionally do not dispatch a graph update:

- A push-validation run on PackageMirror `master` only builds and tests the mirror
  implementation; it does not rebuild mirrored packages or schemas.
- A run with `docs_drift_check_only: true` verifies generated documentation without
  performing the normal mirror update.

The PackageMirror `PACKAGE_MIRROR_TOKEN` must have `Contents: write` access to this
repository so it can create the repository-dispatch event.

The dependency extractor calls `dotnet msbuild` with `-getProperty` and `-getItem`,
so imported props/targets and conditional items are evaluated by MSBuild rather than
guessed from XML. If a repository needs more than one CI evaluation mode, declare
them under `evaluations` in `repositories.yml`; their references are merged while
the evaluation names remain attached to each edge.

## Outputs

| Path | Purpose | Commit? |
| --- | --- | --- |
| `repositories.yml` | Explicit source repository registry and MSBuild evaluations | Yes |
| `graphs/repositories.json` | Detailed deterministic .NET dependency data | Yes |
| `graphs/repositories.mmd` | GitHub-renderable Mermaid source | Yes |
| `graphs/repositories.svg` | Standalone repository overview | Yes |
| `graphify-out/graph.json` | Merged code knowledge graph | Yes |
| `graphify-out/graph.html` | Interactive code graph | Yes |
| `.graphify-cache/` | Per-repository extraction cache | No |

`.gitignore` keeps runtime state, environments, test caches, temporary Graphify
files, and `.graphify-cache/` out of commits while deliberately leaving the final
`graphs/` and `graphify-out/` artifacts visible to Git.
`.graphifyignore` is separate: it prevents Graphify from recursively indexing
the generated graph data if this repository itself is scanned. Source-repository
scans continue to respect each source repository's own ignore rules.

Code-only checks may still be run locally; they do not update graph artifacts:

```bash
python3 -m unittest discover -s tests -v
```

## Query the graph through remote MCP

The repository deploys the committed `graphify-out/graph.json` as a query-only
[Graphify Streamable HTTP MCP](https://github.com/Graphify-Labs/graphify/issues/1143)
service. The service uses the built-in Graphify server; it does not clone source
repositories, regenerate artifacts, or expose an update operation. A new immutable
Cloud Run revision is deployed after a graph change reaches `main`.

The endpoint is:

```text
POST https://<cloud-run-service>/mcp
Authorization: Bearer <QAAS_GRAPH_MCP_TOKEN>
```

It exposes these graph-query tools: `query_graph`, `get_node`, `get_neighbors`,
`get_community`, `god_nodes`, `graph_stats`, and `shortest_path`. Graphify also
advertises `list_prs`, `get_pr_impact`, and `triage_prs`; disable those three
client-side when the connection should be graph-only.

### Codex

Set `QAAS_GRAPH_MCP_TOKEN` in the environment used to launch Codex, then add:

```toml
[mcp_servers.qaas_graph]
url = "https://<cloud-run-service>/mcp"
bearer_token_env_var = "QAAS_GRAPH_MCP_TOKEN"
required = true
enabled = true
enabled_tools = [
  "query_graph",
  "get_node",
  "get_neighbors",
  "get_community",
  "god_nodes",
  "graph_stats",
  "shortest_path",
]
default_tools_approval_mode = "auto"
startup_timeout_sec = 10
tool_timeout_sec = 60
```

Codex CLI, the IDE extension, and the ChatGPT desktop app share this MCP
configuration. Restart the client and use `/mcp` to confirm the connection.

### Claude

Add a custom **Web** connector under **Customize → Connectors**, provide the
Cloud Run `/mcp` URL, choose fixed-header authentication, and add:

```text
Authorization: Bearer <QAAS_GRAPH_MCP_TOKEN>
```

Disable the three PR-analysis tools in the connector tool controls.

### Cloud Run provisioning

The deployment workflow expects these GitHub Actions repository variables:

| Variable | Value |
| --- | --- |
| `GCP_PROJECT_ID` | Google Cloud project containing the service |
| `GCP_REGION` | `me-west1` |
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | Full Workload Identity provider resource name |
| `GCP_DEPLOY_SERVICE_ACCOUNT` | Service account impersonated by GitHub Actions |
| `GCP_ARTIFACT_REPOSITORY` | `qaas-graph` |
| `CLOUD_RUN_SERVICE` | `qaas-graph-mcp` |

Provision an Artifact Registry Docker repository named `qaas-graph`, a runtime
service account named `qaas-graph-mcp`, and a Secret Manager secret named
`qaas-graph-mcp-api-key`. Give the runtime account access only to that secret.
The GitHub deployment account needs permission to push to Artifact Registry,
deploy and route Cloud Run revisions, impersonate the runtime account, and read
the API-key secret for the post-deployment smoke test. Configure GitHub access
through Workload Identity Federation rather than a service-account key.

The service runs in `me-west1` with one warm instance, one vCPU, 512 MiB RAM,
and a maximum of three instances. Cloud Run accepts public HTTPS requests so
standard MCP clients can reach it; Graphify rejects requests without the API
key. Rotate the key by adding a new version to `qaas-graph-mcp-api-key`, running
the deployment workflow, and then updating client credentials.

The workflow smoke-tests authentication, the MCP handshake, graph tool results,
the exact node and edge counts from the committed artifact, source evidence, and
warm-query latency. If the remote smoke test fails, traffic is restored to the
previous serving revision. Use Cloud Run request count, latency, instance count,
and 4xx/5xx metrics for operational monitoring.

## CI

`.github/workflows/update-graphs.yml` is the only supported graph-generation entry
point. It rebuilds the graphs on demand or after a `qaas-package-mirror-synced`
dispatch. It records each checked-out source commit in `graphs/repositories.json`
and opens or updates an `automation/update-graphs` pull request when committed
artifacts change. Private source repositories require a `QAAS_GRAPH_TOKEN`
repository secret with read access; public repositories can use the workflow token.

`.github/workflows/deploy-pages.yml` publishes `graphify-out/graph.html` as the
site root whenever generated graph artifacts or the deployment workflow change on
`main`. The live site is available at <https://thesmoketeam.github.io/qaas-graph/>.

`.github/workflows/deploy-mcp.yml` independently packages the committed graph and
deploys the query-only MCP service. It never generates or modifies graph artifacts.
