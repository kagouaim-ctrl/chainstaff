#!/bin/sh
# Rebuild web/vendor/gramjs.min.js (the browser Telegram client used by the console's chat page).
set -e
cd "$(dirname "$0")/gramjs"
npm install --no-save --no-package-lock telegram@2.26.22 qrcode-generator@1.4.4 esbuild@0.25 buffer process os-browserify path-browserify util events
node build.mjs
