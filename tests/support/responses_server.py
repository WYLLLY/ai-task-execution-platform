"""Independent synthetic Responses HTTP boundary; never accepts live credentials."""

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from tests.unit.test_responses import response


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--journal", required=True)
    args = parser.parse_args()
    journal = Path(args.journal)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass

        def do_POST(self) -> None:
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            scenario = payload.get("metadata", {}).get("scenario", "success")
            assert self.headers.get("Authorization") == "Bearer synthetic-test-key"
            with journal.open("a") as file:
                file.write(
                    json.dumps(
                        {
                            "kind": "request",
                            "path": self.path,
                            "project": self.headers.get("OpenAI-Project"),
                            "transmission": self.headers.get("X-Client-Request-Id"),
                            "payload": payload,
                        }
                    )
                    + "\n"
                )
                file.flush()
            status = {"redirect": 307, "429": 429}.get(scenario, 200)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Retry-After", "3")
            self.send_header("Location", "/redirect-target")
            self.end_headers()
            if status != 200:
                return
            raw = response()
            if any(item.get("type") == "function_call_output" for item in payload.get("input", [])):
                raw["output"] = [
                    {
                        "type": "message",
                        "id": "msg_final",
                        "status": "completed",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "annotations": [],
                                "text": json.dumps(
                                    {
                                        "type": "report",
                                        "title": "Synthetic report",
                                        "summary": "Synthetic data only",
                                        "citations": [],
                                    }
                                ),
                            }
                        ],
                    }
                ]
            if scenario == "missing_usage":
                raw["usage"] = None
            body = json.dumps(raw).encode()
            if scenario == "malformed":
                body = b'{"secret":"canary",'
            if scenario == "oversized":
                body = b"x" * 1048577
            try:
                if scenario == "slow":
                    for b in body:
                        self.wfile.write(bytes([b]))
                        self.wfile.flush()
                        time.sleep(0.02)
                else:
                    self.wfile.write(body)
            except BrokenPipeError, ConnectionResetError:
                pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    print(server.server_port, flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
