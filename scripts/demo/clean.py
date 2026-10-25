"""Step 3 of scripts/demo/README.md: replace local addresses, refuse anything private, drop duplicate feed messages."""
import json, re, sys
src, dst = sys.argv[1], sys.argv[2]
s = open(src).read()
s = re.sub(r'ws://127\.0\.0\.1:\d+', 'wss://demo-node（示例节点）', s)
s = s.replace('"http://a"', '"https://demo-rpc（示例节点）"')
for bad in ("127.0.0.1", "/tmp/", "/home/", "tttttttttttt", "IndexError", "pop from empty"):
    assert bad not in s, bad
d = json.loads(s)
seen, ws = set(), []
for m in d["ws"]:
    k = (m.get("type"), m.get("seq"))
    if k not in seen:
        seen.add(k); ws.append(m)
d["ws"] = ws
json.dump(d, open(dst, "w"), ensure_ascii=False, separators=(",", ":"))
print("ok", len(d["get"]), len(ws))
