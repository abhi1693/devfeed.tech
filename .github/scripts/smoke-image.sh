#!/usr/bin/env bash
set -euo pipefail
bash scripts/ci/smoke-image.sh "$1" "$CI_IMAGE_COMPONENT" "${2#linux/}" "$3"
