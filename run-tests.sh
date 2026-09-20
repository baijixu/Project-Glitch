#!/usr/bin/env sh
# Runs the whole test suite with Brain's own virtualenv. Extra arguments go to unittest,
# e.g.  ./run-tests.sh tests.test_memory   or   ./run-tests.sh -v
cd "$(dirname "$0")" || exit 1
exec brain/.venv/bin/python -m unittest discover -s tests -t . "$@"
