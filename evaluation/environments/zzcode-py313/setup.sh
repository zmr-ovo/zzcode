#!/bin/sh
set -eu

python -m pip install --no-cache-dir --requirement /opt/zzcode-eval/requirements.lock
python -m pytest --version
git --version
