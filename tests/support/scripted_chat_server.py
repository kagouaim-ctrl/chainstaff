"""A scripted OpenAI-compatible chat endpoint for checking scripts/ai_check.py end to end in CI. It is not a model:
it plans, calls wallet_analysis / block_info and answers by fixed rules, so results from it are mock results."""
import json, re, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ADDR = re.compile(r"0x[0-9a-fA-F]{40}")

def answer(body):
    msgs = body["messages"]
    usage = {"prompt_tokens": 900, "completion_tokens": 120}
    if "JSON Schema" in msgs[0]["content"]:
        req = json.loads(msgs[1]["content"])
        team = req["team"]
        addr = ADDR.search(req["goal"]).group(0)
        a = next(t for t in team if "wallet_analysis" in t["skills"])
        m = next(t for t in team if "block_info" in t["skills"])
        r = next(t for t in team if "data_report" in t["skills"])
        steps = [{"key": "a", "title": "analyse", "skill": "agent_task", "agent_id": a["agent_id"], "input_json": json.dumps({"instruction": f"Read {addr}"}), "depends_on": []},
                 {"key": "m", "title": "monitor", "skill": "agent_task", "agent_id": m["agent_id"], "input_json": json.dumps({"instruction": "Read the latest block"}), "depends_on": []},
                 {"key": "r", "title": "report", "skill": "agent_task", "agent_id": r["agent_id"], "input_json": json.dumps({"instruction": "Write the report"}), "depends_on": ["a", "m"]}]
        return {"model": body["model"], "choices": [{"message": {"role": "assistant", "content": json.dumps({"steps": steps})}, "finish_reason": "stop"}], "usage": usage}
    tool_msgs = [m for m in msgs if m["role"] == "tool"]
    names = [t["function"]["name"] for t in body.get("tools", [])]
    if names and not tool_msgs:
        user = next(m["content"] for m in msgs if m["role"] == "user")
        found = ADDR.search(user)
        if "wallet_analysis" in names and found:
            call = ("wallet_analysis", {"address": found.group(0)})
        elif "block_info" in names:
            call = ("block_info", {})
        else:
            call = None
        if call:
            return {"model": body["model"], "choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_1", "type": "function", "function": {"name": call[0], "arguments": json.dumps(call[1])}}]}, "finish_reason": "tool_calls"}], "usage": usage}
    bnb = re.search(r'"bnb": "([0-9.]+)"', "".join(m["content"] for m in tool_msgs))
    text = f"余额 {bnb.group(1)} BNB" if bnb else "报告：链上事实 / 统计 / 推断"
    return {"model": body["model"], "choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}], "usage": usage}

class H(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        out = json.dumps(answer(body)).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)
    def log_message(self, *a): pass

ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
