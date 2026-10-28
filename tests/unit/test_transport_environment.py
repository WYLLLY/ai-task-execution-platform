"""Actual TLS context configuration must be independent of inherited network settings."""

import ssl
from pathlib import Path

import certifi
import httpx
import pytest

from task_platform.report_transport import ReportTransport
from task_platform.transport import SimulationTransport


@pytest.fixture
def trust_bundle(tmp_path: Path) -> tuple[Path, dict[str, int]]:
    bundle = Path(certifi.where()).read_text()
    start = bundle.index("-----BEGIN CERTIFICATE-----")
    end = bundle.index("-----END CERTIFICATE-----", start) + len("-----END CERTIFICATE-----")
    environment_bundle = tmp_path / "valid-single-root.pem"
    environment_bundle.write_text(bundle[start:end] + "\n")
    expected = ssl.create_default_context(cafile=certifi.where()).cert_store_stats()
    assert expected["x509_ca"] > 1
    assert (
        ssl.create_default_context(cafile=str(environment_bundle)).cert_store_stats()["x509_ca"]
        == 1
    )
    return environment_bundle, expected


@pytest.mark.parametrize("adapter", ["simulation", "report"])
async def test_explicit_transport_ignores_valid_environment_trust_bundle(
    adapter: str, monkeypatch: pytest.MonkeyPatch, trust_bundle: tuple[Path, dict[str, int]]
) -> None:
    environment_bundle, expected = trust_bundle
    monkeypatch.setenv("SSL_CERT_FILE", str(environment_bundle))
    transport = (
        SimulationTransport("http://127.0.0.1")
        if adapter == "simulation"
        else ReportTransport("http://127.0.0.1", "synthetic-service-key-" * 2)
    )
    try:
        http_transport = transport.client._transport
        assert isinstance(http_transport, httpx.AsyncHTTPTransport)
        context = http_transport._pool._ssl_context
        assert isinstance(context, ssl.SSLContext)
        assert context.cert_store_stats() == expected
    finally:
        await transport.client.aclose()
