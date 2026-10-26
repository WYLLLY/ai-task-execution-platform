"""Exercise the running disposable M1 + M2 Compose stack, including a real Redis outage."""

import json
import os
import shlex
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx

COMPOSE = shlex.split(os.environ.get("COMPOSE", "docker compose")) + ["-p", "atp-m1-test"]


def command(args: list[str]) -> str:
    result = subprocess.run(args, capture_output=True, text=True, check=False)  # noqa: S603 -- operator-selected local Docker CLI, never request/model data
    if result.returncode:
        raise RuntimeError("Smoke command failed; inspect the local Compose service state")
    return result.stdout


def compose(*args: str) -> str:
    return command([*COMPOSE, *args])


def require(condition: bool, label: str) -> None:
    if not condition:
        raise RuntimeError(f"Smoke check failed: {label}")


def inspect(service: str) -> dict[str, Any]:
    container = compose("ps", "-q", service).strip()
    records: list[dict[str, Any]] = json.loads(command(["docker", "inspect", container]))
    return records[0]


def main() -> None:
    address = compose("port", "api", "8000").strip()
    with httpx.Client(base_url=f"http://{address}", timeout=8, trust_env=False) as client:
        require(client.get("/health/ready").status_code == 200, "initial readiness")
        principal = json.loads(
            compose(
                "run",
                "--rm",
                "--no-deps",
                "admin",
                "admin",
                "principal-create",
                "--name",
                "Compose smoke",
            )
        )["principal_id"]
        issued = json.loads(
            compose(
                "run", "--rm", "--no-deps", "admin", "admin", "key-create", "--principal", principal
            )
        )
        token = issued["api_key"]
        headers = {"Authorization": f"Bearer {token}"}
        require(client.get("/v1/me", headers=headers).status_code == 200, "provisioned key")
        compose("run", "--rm", "--no-deps", "admin", "admin", "admission", "--enabled", "true")
        headers["X-Execution-Epoch"] = client.get("/v1/system").json()["execution_epoch"]
        body = {
            "workflow": {"name": "analysis", "version": "1"},
            "input": {"texts": ["Synthetic restart and response-loss fixture"]},
        }
        encoded = json.dumps(body).encode()
        lost_headers = {**headers, "Idempotency-Key": "lost-response"}
        host, port = address.rsplit(":", 1)
        # Send a physical HTTP request but deliberately never read its response.
        with socket.create_connection((host, int(port)), timeout=8) as connection:
            head = (
                "POST /v1/tasks HTTP/1.1\r\nHost: localhost\r\n"
                "Content-Type: application/json\r\n"
                f"Content-Length: {len(encoded)}\r\n"
                + "".join(f"{key}: {value}\r\n" for key, value in lost_headers.items())
                + "\r\n"
            )
            connection.sendall(head.encode() + encoded)
            for _ in range(30):
                items = client.get("/v1/tasks", headers=headers).json()["items"]
                if items:
                    break
                time.sleep(0.1)
            else:
                raise RuntimeError("Lost-response command was not committed")
        require(len(items) == 1, "one durable task after lost HTTP response")
        task_id = items[0]["task_id"]
        replay = client.post("/v1/tasks", json=body, headers=lost_headers)
        require(
            replay.status_code == 202 and replay.json()["task_id"] == task_id,
            "lost response replays original acceptance",
        )
        compose("restart", "postgres", "api")
        for _ in range(60):
            try:
                if client.get("/health/ready").status_code == 200:
                    break
            except httpx.TransportError:
                pass
            time.sleep(0.2)
        else:
            raise RuntimeError("PostgreSQL/API restart did not recover")
        restarted = client.post("/v1/tasks", json=body, headers=lost_headers)
        require(
            restarted.status_code == 202 and restarted.content == replay.content,
            "PostgreSQL and API restart preserve exact response",
        )
        try:
            compose("stop", "redis")
            ready = client.get("/health/ready")
            require(
                ready.status_code == 200 and ready.json()["redis"] is False, "Redis degradation"
            )
            require(ready.json()["submission_available"] is False, "submission closed")
            require(client.get("/v1/me", headers=headers).status_code == 200, "reads during outage")
            denied = client.post(
                "/v1/tasks", json=body, headers={**headers, "Idempotency-Key": "redis-down-new"}
            )
            require(denied.status_code == 503, "Redis outage rejects only new admission")
            retained = client.post("/v1/tasks", json=body, headers=lost_headers)
            require(
                retained.status_code == 202 and retained.content == replay.content,
                "Redis outage preserves exact replay",
            )
            cancelled = client.post(
                f"/v1/tasks/{task_id}/cancel",
                json={"expected_task_version": 1},
                headers={**headers, "Idempotency-Key": "outage-cancel"},
            )
            require(
                cancelled.status_code == 200 and cancelled.json()["state"] == "cancelled",
                "control remains available without Redis",
            )
            steps = client.get(f"/v1/tasks/{task_id}/steps", headers=headers).json()["items"]
            require(all(step["state"] == "cancelled" for step in steps), "no executable children")
        finally:
            compose("start", "redis")
        for _ in range(30):
            if client.get("/health/ready").json()["redis"]:
                break
            time.sleep(0.2)
        else:
            raise RuntimeError("Redis did not recover")
        recovered = client.post(
            "/v1/tasks", json=body, headers={**headers, "Idempotency-Key": "redis-down-new"}
        )
        require(recovered.status_code == 202, "Redis recovery reopens new admission")
        client.post(
            f"/v1/tasks/{recovered.json()['task_id']}/cancel",
            json={"expected_task_version": 1},
            headers={**headers, "Idempotency-Key": "cleanup"},
        ).raise_for_status()
        compose("run", "--rm", "--no-deps", "admin", "admin", "admission", "--enabled", "false")
        compose(
            "run",
            "--rm",
            "--no-deps",
            "admin",
            "admin",
            "key-revoke",
            "--principal",
            principal,
            "--key",
            issued["key_id"],
        )
        require(client.get("/v1/me", headers=headers).status_code == 401, "revocation")
        canary = "M1_SYNTHETIC_SECRET_CANARY"
        require(
            canary
            not in client.get(
                "/v1/me?secret=" + canary, headers={"Authorization": "Bearer " + canary}
            ).text,
            "response redaction",
        )
        logs = compose("logs", "--no-color", "api")
        require(canary not in logs and token not in logs, "HTTP log redaction")
        for secret in Path(".secrets").glob("*_password"):
            require(secret.read_text().strip() not in logs, "credential log redaction")
    for service in ("api", "postgres", "redis"):
        state = inspect(service)
        require(state["Config"]["User"] not in ("", "root", "0", "0:0"), "non-root " + service)
        require("ALL" in state["HostConfig"]["CapDrop"], "capabilities " + service)
        require(state["HostConfig"]["Memory"] > 0, "memory cap " + service)
        require(state["HostConfig"]["NanoCpus"] > 0, "CPU cap " + service)
        require(
            any("no-new-privileges" in item for item in state["HostConfig"]["SecurityOpt"]),
            "privileges " + service,
        )
        if service != "api":
            require(not any(state["NetworkSettings"]["Ports"].values()), "private ports " + service)
        else:
            require(state["HostConfig"]["ReadonlyRootfs"], "read-only API")
            require(
                not any("OPENAI" in item for item in state["Config"]["Env"]),
                "no provider credential",
            )
            require(
                {Path(item["Destination"]).name for item in state["Mounts"]}
                == {"api_database_url", "redis_url"},
                "role-scoped secret mounts",
            )
            private_networks = 0
            for network_id in state["NetworkSettings"]["Networks"]:
                net = json.loads(command(["docker", "network", "inspect", network_id]))[0]
                private_networks += int(net["Internal"])
            require(private_networks == 1, "private backend network")
            ports = state["NetworkSettings"]["Ports"]["8000/tcp"]
            require(
                bool(ports) and all(p["HostIp"] == "127.0.0.1" for p in ports),
                "loopback-only ingress",
            )
    worker = json.loads(compose("run", "--rm", "--no-deps", "worker"))
    require(worker["ready"] and not worker["execution_implemented"], "worker preflight only")
    print(
        "PASS: M1+M2 Compose identity, lost HTTP response, PostgreSQL/API restart, "
        "Redis outage/recovery, command replay/cancel, revocation, redaction, "
        "isolation, limits and worker preflight"
    )


if __name__ == "__main__":
    main()
