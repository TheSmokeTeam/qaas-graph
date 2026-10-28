#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd -- "$script_dir/.." && pwd)"
registry="$repo_dir/repositories.yml"
force=""

usage() {
  cat <<'EOF'
Usage: scripts/generate.sh [--force]

  --force  Force Graphify to rescan every repository
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --force) force="--force" ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

if [[ "${GITHUB_ACTIONS:-}" != "true" || "${QAAS_GRAPH_CI:-}" != "true" ]]; then
  cat >&2 <<'EOF'
Graphify generation is CI-only.
Run the "Update ecosystem graphs" workflow in GitHub Actions instead:
https://github.com/TheSmokeTeam/qaas-graph/actions/workflows/update-graphs.yml
EOF
  exit 1
fi

cd "$repo_dir"

if ! command -v graphify >/dev/null 2>&1; then
  echo "graphify is required. Install pinned dependencies with: python3 -m pip install -r requirements.txt" >&2
  exit 1
fi

mkdir -p "$repo_dir/.graphify-cache" "$repo_dir/graphify-out"
graph_files=()
while IFS=$'\t' read -r name path; do
  output="$repo_dir/.graphify-cache/$name"
  graphify extract "$path" --code-only --out "$output" $force
  graph_file="$output/graphify-out/graph.json"
  if [[ ! -f "$graph_file" ]]; then
    echo "Graphify did not create $graph_file" >&2
    exit 1
  fi
  graph_files+=("$graph_file")
done < <(python3 scripts/list_repositories.py --registry "$registry")

graphify merge-graphs "${graph_files[@]}" --out "$repo_dir/graphify-out/graph.json"
graphify export html --graph "$repo_dir/graphify-out/graph.json"
