#!/usr/bin/env bash
# Preserve a finished run under runs/demo-final/<name> so `done replay` can show it with
# zero LLM calls. Everything else in runs/ is gitignored.
set -euo pipefail
run_dir="${1:?usage: ./save_demo.sh runs/<id> <name>}"
name="${2:?usage: ./save_demo.sh runs/<id> <name>}"
dest="runs/demo-final/$name"
rm -rf "$dest"; mkdir -p "$dest"
cp "$run_dir"/tree.json "$run_dir"/events.jsonl "$dest"/
[ -f "$run_dir/goal.txt" ]   && cp "$run_dir/goal.txt" "$dest"/
[ -f "$run_dir/report.json" ] && cp "$run_dir/report.json" "$dest"/
# the artifacts the agents actually produced, so a judge can read them
[ -d "$run_dir/workspace" ] && cp -R "$run_dir/workspace" "$dest"/workspace
echo "saved $run_dir -> $dest"
du -sh "$dest"
