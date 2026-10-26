"""Exercise the running disposable M1 Compose stack, including a real Redis outage."""

import json
import os
import shlex
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
        try:
            compose("stop", "redis")
            ready = client.get("/health/ready")
            require(
                ready.status_code == 200 and ready.json()["redis"] is False, "Redis degradation"
            )
            require(ready.json()["submission_available"] is False, "submission closed")
            require(client.get("/v1/me", headers=headers).status_code == 200, "reads during outage")
        finally:
            compose("start", "redis")
        for _ in range(30):
            if client.get("/health/ready").json()["redis"]:
                break
            time.sleep(0.2)
        else:
            raise RuntimeError("Redis did not recover")
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
        "PASS: Compose identity, real Redis outage/recovery, revocation, redaction, "
        "isolation, limits and worker preflight"
    )


if __name__ == "__main__":
    main()
