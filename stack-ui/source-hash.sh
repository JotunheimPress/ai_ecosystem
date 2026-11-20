#!/usr/bin/env bash
# Prints a short hash of everything the stack-ui image is built from: the Containerfile
# and every file in this directory the build context takes in.
#
# The dashboard's code is baked into its image, so an image built before `git pull` keeps
# serving the old dashboard. run-podman.sh stamps this hash on the image as the label
# ai-stack.source, and `stack install`, `stack doctor` and run-podman.sh compare the two
# to tell a current image from a stale one.
#
# The exclusions follow .containerignore. Hashing a file the build doesn't use costs at
# most a needless rebuild; missing one it does use is the bug this exists to prevent, so
# when in doubt a file stays in.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

find . -type f \
  ! -path './.venv/*' ! -path './venv/*' ! -path '*/__pycache__/*' ! -name '*.pyc' \
  ! -path './.pytest_cache/*' ! -path './tests/*' ! -path './docs/*' \
  ! -name '*.md' ! -name '*.log' ! -name 'tokens.env' ! -name 'wapyt-*.whl' \
  ! -name '.gitignore' ! -name '.containerignore' ! -name 'run-podman.sh' \
  ! -name 'source-hash.sh' -print0 \
  | LC_ALL=C sort -z | xargs -0 sha256sum | sha256sum | cut -c1-16
