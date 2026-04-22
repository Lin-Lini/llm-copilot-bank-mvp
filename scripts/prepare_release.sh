#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RELEASE_DIR="$ROOT/../llm-copilot-bank-release"
ARCHIVE_NAME="llm-copilot-bank-clean.zip"

rm -rf "$RELEASE_DIR"
mkdir -p "$RELEASE_DIR"

rsync -a \
  --exclude ".git" \
  --exclude ".env" \
  --exclude "__pycache__" \
  --exclude "*.pyc" \
  --exclude "*.pyo" \
  --exclude ".DS_Store" \
  --exclude "__MACOSX" \
  "$ROOT/" "$RELEASE_DIR/llm-copilot-bank-git/"

cd "$RELEASE_DIR"
zip -r "$ARCHIVE_NAME" "llm-copilot-bank-git" \
  -x "*/.git/*" "*/__pycache__/*" "*.pyc" "*.pyo" "*/.DS_Store" "*/__MACOSX/*"

echo "Created: $RELEASE_DIR/$ARCHIVE_NAME"