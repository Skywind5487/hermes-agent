# Issue #60 — Stage 5 run

Run against the canonical recovered DB from the #60 worktree:

```bash
cd ~/hermes-benchmark/hermes-issue60
git fetch origin research/chatgpt-import-provenance-60:refs/remotes/origin/research/chatgpt-import-provenance-60
git switch --detach refs/remotes/origin/research/chatgpt-import-provenance-60
git rev-parse --short HEAD
python3 research/chatgpt-import/extract_issue60_stage5.py
```

Copy the resulting archive to Windows:

```bash
mkdir -p /mnt/c/Users/weiti/Downloads/hermes-issue60-stage5
latest="$(ls -1t issue60-stage5-evidence-*.tar.gz | head -n1)"
cp -- "$latest" /mnt/c/Users/weiti/Downloads/hermes-issue60-stage5/
echo "/mnt/c/Users/weiti/Downloads/hermes-issue60-stage5/$(basename "$latest")"
```

Stage 5 is DB-only and privacy-bounded: it emits hashes/lengths for sampled duplicate/orphan content, not raw private message text.
