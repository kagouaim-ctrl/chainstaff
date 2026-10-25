# Static demo snapshot

The KAGOU Console can be built as a static page that needs no server (`VITE_DEMO=1`, see
`web/terminal/src/lib/demo.ts`). Every request is answered from `demo-data.json`, a snapshot recorded from a
server running on **sample data**: the tests' scripted model instead of Claude, and simulated chains. Nothing in
it is real or live, and nothing can be changed. The header says "演示 · 示例数据".

```bash
cd web/terminal && npm run build && cd ../..           # the normal console, served by the seed server
python scripts/demo/seed_server.py &                   # 1. sample data on 127.0.0.1:8766
node scripts/demo/record.js /tmp/raw.json              # 2. visit every page, record the answers
python scripts/demo/clean.py /tmp/raw.json /tmp/demo-data.json   # 3. scrub local addresses
cd web/terminal && npm run build:demo                  # 4. dist-demo/, built for /demo/
cp /tmp/demo-data.json dist-demo/demo-data.json
```

Then copy `dist-demo/` to `public/demo/` in the KAGOUAI repository (www.kagouai.com/demo/).
