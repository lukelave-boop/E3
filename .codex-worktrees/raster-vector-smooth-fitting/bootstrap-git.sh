#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if [[ -d .git ]]; then
  echo "This directory is already a Git repository."
  git status --short
  exit 0
fi

if ! command -v git >/dev/null 2>&1; then
  echo "Git is not installed. Run ./install.sh or install git first." >&2
  exit 1
fi

name="$(git config --global user.name || true)"
email="$(git config --global user.email || true)"

if [[ -z "$name" ]]; then
  if [[ -t 0 ]]; then
    read -r -p "Git display name: " name
  else
    echo "No Git user.name is configured. Run: git config --global user.name 'Your Name'" >&2
    exit 2
  fi
fi
if [[ -z "$email" ]]; then
  if [[ -t 0 ]]; then
    read -r -p "Git email (a GitHub no-reply address is fine): " email
  else
    echo "No Git user.email is configured. Run: git config --global user.email 'you@example.com'" >&2
    exit 2
  fi
fi
if [[ -z "$name" || -z "$email" ]]; then
  echo "Git name and email cannot be blank." >&2
  exit 2
fi

git init
git branch -M main
git config user.name "$name"
git config user.email "$email"
git add .
git commit -m "Initial laser camera alignment application"

cat <<'EOF'

Local repository created with an initial commit.
After creating an empty GitHub repository, connect and push it with one of:

  git remote add origin git@github.com:YOUR_USERNAME/YOUR_REPOSITORY.git
  git push -u origin main

or

  git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPOSITORY.git
  git push -u origin main
EOF
