#!/usr/bin/bash
# Run lint and the offline test suite in throwaway containers, on the
# oldest supported Python (3.10) and the newest, the same way CI does.
# Lint covers the files written since the repository was created; the older
# scripts predate it.
#
# Usage: tests/docker-test.sh [python-version ...]   (default: 3.10 3)
# The project is mounted read-only; nothing is written to the host.
set -Eeuo pipefail

project="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
versions=("$@")
if [ "${#versions[@]}" -eq 0 ]; then
    versions=(3.10 3)
fi

for version in "${versions[@]}"; do
    echo "=== Python ${version} ==="
    docker run --rm --network bridge \
        --name "mpd-listening-tools-tests-${version//./-}-$$" \
        -v "${project}:/src:ro" -w /tmp \
        "python:${version}-slim" \
        bash -c 'cp -r /src /tmp/project && cd /tmp/project \
            && rm -rf data .env \
            && pip install --quiet --disable-pip-version-check --root-user-action=ignore flake8 \
            && python -m flake8 --max-line-length 100 scrobbles.py tests/*.py \
            && python -m unittest discover -s tests'
done
