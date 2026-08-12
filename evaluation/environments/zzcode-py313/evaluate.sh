#!/bin/sh
set -eu

if [ "$#" -lt 2 ]; then
    echo "usage: evaluate.sh <f2p|p2p> <pytest-selector>..." >&2
    exit 64
fi

group_name=$1
shift
case "$group_name" in
    f2p|p2p) ;;
    *)
        echo "invalid test group: $group_name" >&2
        exit 64
        ;;
esac

mkdir -p "$HOME"
exec python -m pytest \
    -q \
    -p no:cacheprovider \
    "--junitxml=/artifacts/${group_name}.xml" \
    "$@"
