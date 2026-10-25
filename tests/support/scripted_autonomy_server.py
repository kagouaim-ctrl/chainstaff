"""A scripted OpenAI-compatible endpoint for running the autonomy worker end to end without a model key. It is not a
model: it follows the fixed rules in autonomy_brain.decide, so everything it produces is a mock result.

    python tests/support/scripted_autonomy_server.py PORT [DELAY_S]   # DELAY_S: wait before each answer (crash tests)"""
import json, os, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from autonomy_brain import openai_answer  # noqa: E402


class H(BaseHTTPRequestHandler):
    def do_POST(self):
        time.sleep(DELAY)
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        out = json.dumps(openai_answer(body)).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)

    def log_message(self, *a): pass


DELAY = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0

if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
