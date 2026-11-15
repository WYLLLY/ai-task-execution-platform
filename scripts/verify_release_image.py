"""Behavioral artifact check: production contains no package installer tooling."""

import os
import shutil
import subprocess


def main() -> None:
    docker = shutil.which("docker")
    if docker is None:
        raise RuntimeError("Docker CLI required")
    reference = os.environ.get("ATP_RUNTIME_IMAGE", "ai-task-platform:m1")
    identity = subprocess.check_output(  # noqa: S603 -- operator-owned Docker reference
        [docker, "image", "inspect", reference, "--format", "{{.Id}}"], text=True
    ).strip()
    subprocess.run(  # noqa: S603 -- fixed read-only artifact assertion
        [
            docker,
            "run",
            "--rm",
            "--entrypoint",
            "python",
            identity,
            "-c",
            "import importlib.util, ssl; "
            'assert importlib.util.find_spec("pip") is None; '
            'assert importlib.util.find_spec("ensurepip") is None, '
            '"runtime bundles installer wheels"; '
            "import subprocess; "
            "subprocess.run(['/usr/local/bin/python','-c',"
            "'import importlib.util; "
            'assert importlib.util.find_spec("pip") is None\'],check=True); '
            "version = tuple(map(int, ssl.OPENSSL_VERSION.split()[1].split('.'))); "
            'assert version >= (3, 5, 9), "libssl security patch missing"',
        ],
        check=True,
    )
    print("PASS: immutable runtime artifact excludes installer tooling " + identity)


if __name__ == "__main__":
    main()
