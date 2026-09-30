"""A local stand-in for the three model APIs, with scripted faults. Used by the tests only."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeAPI:
    def __init__(self):
        self.queue = {}      # path -> list of actions
        self.requests = []   # (path, headers, body)
        api = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get("content-length", 0))
                body = json.loads(self.rfile.read(n) or b"{}")
                api.requests.append((self.path, dict(self.headers), body))
                actions = api.queue.get(self.path) or []
                act = actions.pop(0) if actions else {"status": 500, "body": {"error": "queue empty"}}
                if act.get("close"):
                    self.close_connection = True
                    return
                if act.get("delay"):
                    time.sleep(act["delay"])
                raw = act["raw"].encode() if "raw" in act else json.dumps(act.get("body", {})).encode()
                try:
                    self.send_response(act.get("status", 200))
                    for k, v in (act.get("headers") or {}).items():
                        self.send_header(k, v)
                    self.send_header("content-type", "application/json")
                    self.send_header("content-length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def openai_ok(obj=None, content=None, finish="stop", usage=None):
    return {"status": 200, "body": {
        "choices": [{"message": {"content": content if content is not None else json.dumps(obj)},
                     "finish_reason": finish}],
        "usage": usage or {"prompt_tokens": 1000, "completion_tokens": 100,
                           "prompt_tokens_details": {"cached_tokens": 200},
                           "completion_tokens_details": {"reasoning_tokens": 60}}}}


def deepseek_ok(obj=None, content=None, finish="stop"):
    return {"status": 200, "body": {
        "choices": [{"message": {"content": content if content is not None else json.dumps(obj)},
                     "finish_reason": finish}],
        "usage": {"prompt_tokens": 1000, "completion_tokens": 50, "prompt_cache_hit_tokens": 0,
                  "prompt_cache_miss_tokens": 1000}}}


def anthropic_ok(obj, stop_reason="tool_use"):
    return {"status": 200, "body": {
        "content": [{"type": "tool_use", "id": "t1", "name": "record_fields", "input": obj}],
        "stop_reason": stop_reason, "usage": {"input_tokens": 1000, "output_tokens": 80}}}
