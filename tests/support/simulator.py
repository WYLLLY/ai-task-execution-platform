"""Separate-process physical HTTP oracle; journal is independent of PostgreSQL."""

import argparse
import json
import os
import threading
import time
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def serve(journal: Path) -> None:
    lock = threading.Lock()

    def record(kind: str, call: str) -> None:
        with lock, journal.open("a") as stream:
            stream.write(json.dumps({"kind": kind, "call": call}) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return

        def do_POST(self) -> None:
            call = self.headers.get("X-Transmission-ID", "")
            record("request", call)
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            mode = data.get("scenario", data.get("texts", ["success"])[0])
            if mode not in {"retryable", "rejected", "retry_after", "retry_after_date"}:
                record("effect", call)
            if mode == "drop":
                self.connection.close()
                return
            body = json.dumps({"outcome": "succeeded", "cost": 20, "tokens": 10}).encode()
            status = 200
            if mode == "redirect":
                status = 307
            elif mode == "malformed":
                body = b"{"
            elif mode == "oversized":
                body = b"x" * 1048577
            elif mode == "missing_usage":
                body = b'{"outcome":"succeeded"}'
            elif mode in {"retryable", "rejected"}:
                body = json.dumps({"outcome": mode, "cost": 0, "tokens": 0}).encode()
            elif mode in {"retry_after", "retry_after_date"}:
                body = b'{"outcome":"retryable","cost":0,"tokens":0}'
            elif mode in {"rate_limited", "bad_retry_after", "huge_retry_after"}:
                status = 429
            self.send_response(status)
            if mode in {"retry_after", "rate_limited"}:
                self.send_header("Retry-After", "30")
            elif mode == "retry_after_date":
                self.send_header(
                    "Retry-After", format_datetime(datetime.now(UTC) + timedelta(seconds=30))
                )
            elif mode == "bad_retry_after":
                self.send_header("Retry-After", "invalid")
            elif mode == "huge_retry_after":
                self.send_header("Retry-After", "9" * 100)
            if mode == "redirect":
                self.send_header("Location", f"http://127.0.0.1:{server.server_port}/trap")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                if mode == "slow":
                    for byte in body:
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        time.sleep(0.03)
                else:
                    self.wfile.write(body)
            except BrokenPipeError, ConnectionResetError:
                return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    print(server.server_port, flush=True)
    server.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--journal", type=Path, required=True)
    serve(parser.parse_args().journal)
