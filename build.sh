#!/bin/sh
set -eu
cd "$(dirname "$0")/tui"
mkdir -p ../bin
"${GO:-go}" build -trimpath -o ../bin/samatha-tui .
