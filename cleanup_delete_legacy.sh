#!/usr/bin/env bash
set -euo pipefail
FILES=(
  "routes/conversation.py"
  "routes/legacy_block.py"
)
for f in "${FILES[@]}"; do
  if [ -e "$f" ]; then
    echo "Removing $f"
    git rm -f "$f" 2>/dev/null || rm -f "$f"
  else
    echo "Already removed: $f"
  fi
done
echo "Done."
