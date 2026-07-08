import base64

import pytest
from kubernetes.client.exceptions import ApiException

from octonaut_operator.handlers import (
    Clients,
    _apply,
    _read_existing_password,
    reconcile_tradingagent,
    resolve_database_ref,
)

MINIMAL_SPEC = {
    "openrouter": {
        "model": "poolside/laguna-m.1",
        "apiKey": {"secretKeyRef": {"name": "minisaurus-secret", "key": "openrouter-key"}},
    },
    "strategy": {"type": "GRID", "ticker": "BTCUSD", "balance": 50000, "prompt": "Trade.\n"},
}


# --------------------------------------------------------------------------- #
# resolve_database_ref -- pure decision logic
# --------------------------------------------------------------------------- #


def test_resolve_database_ref_passes_through_user_supplied_postgres():
    spec = {**MINIMAL_SPEC, "postgres": {"databaseUrl": {"secretKeyRef":
             {"name": "my-db", "key": "url"}}}}
    ref, pg = resolve_database_ref(spec, "minisaurus", "default", existing_password=None)
    assert ref == {"name": "my-db", "key": "url"}
    assert pg is None


def test_resolve_database_ref_provisions_default_when_omitted():
    ref, pg = resolve_database_ref(MINIMAL_SPEC, "minisaurus", "default", existing_password=None)
    assert ref == {"name": "minisaurus-db", "key": "DATABASE_URL"}
    assert pg is not None
    assert pg["deployment"]["spec"]["template"]["spec"]["containers"][0]["image"] == \
        "pgvector/pgvector:pg17"


def test_resolve_database_ref_reuses_existing_password_across_reconciles():
    ref, pg = resolve_database_ref(MINIMAL_SPEC, "minisaurus", "default",
                                     existing_password="stable-pw")
    assert "stable-pw" in pg["secret"]["stringData"]["DATABASE_URL"]


# --------------------------------------------------------------------------- #
# _read_existing_password
# --------------------------------------------------------------------------- #


class _FakeSecret:
    def __init__(self, password: str):
        self.data = {"POSTGRES_PASSWORD": base64.b64encode(password.encode()).decode()}


class _FakeCoreV1:
    def __init__(self, secret=None, status=404):
        self._secret = secret
        self._status = status
        self.calls = []

    def read_namespaced_secret(self, name, namespace):
        self.calls.append((name, namespace))
        if self._secret is None:
            raise ApiException(status=self._status)
        return self._secret


def test_read_existing_password_returns_none_when_secret_missing():
    core_v1 = _FakeCoreV1(secret=None, status=404)
    assert _read_existing_password(core_v1, "minisaurus-db", "default") is None


def test_read_existing_password_decodes_existing_secret():
    core_v1 = _FakeCoreV1(secret=_FakeSecret("stable-pw"))
    assert _read_existing_password(core_v1, "minisaurus-db", "default") == "stable-pw"


def test_read_existing_password_reraises_non_404_errors():
    core_v1 = _FakeCoreV1(secret=None, status=500)
    with pytest.raises(ApiException):
        _read_existing_password(core_v1, "minisaurus-db", "default")


# --------------------------------------------------------------------------- #
# _apply -- create-or-patch dispatch
# --------------------------------------------------------------------------- #


def test_apply_creates_when_no_conflict():
    calls = []

    def create_fn(namespace, body):
        calls.append(("create", namespace, body))

    def patch_fn(name, namespace, body):
        calls.append(("patch", name, namespace, body))

    _apply(create_fn, patch_fn, "minisaurus", "default", {"k": "v"})
    assert calls == [("create", "default", {"k": "v"})]


def test_apply_patches_on_409_conflict():
    calls = []

    def create_fn(namespace, body):
        raise ApiException(status=409)

    def patch_fn(name, namespace, body):
        calls.append(("patch", name, namespace, body))

    _apply(create_fn, patch_fn, "minisaurus", "default", {"k": "v"})
    assert calls == [("patch", "minisaurus", "default", {"k": "v"})]


def test_apply_reraises_non_409_errors():
    def create_fn(namespace, body):
        raise ApiException(status=500)

    with pytest.raises(ApiException):
        _apply(create_fn, lambda *a: None, "minisaurus", "default", {"k": "v"})


# --------------------------------------------------------------------------- #
# reconcile_tradingagent -- full orchestration against fake clients
# --------------------------------------------------------------------------- #


class _RecordingApi:
    def __init__(self, secret=None):
        self.created = []
        self._secret = secret

    def read_namespaced_secret(self, name, namespace):
        if self._secret is None:
            raise ApiException(status=404)
        return self._secret

    def __getattr__(self, item):
        if item.startswith("create_namespaced_"):
            kind = item[len("create_namespaced_"):]

            def _create(namespace, body):
                self.created.append((kind, body["metadata"]["name"]))

            return _create
        if item.startswith("patch_namespaced_"):
            return lambda name, namespace, body: self.created.append(("patch", name))
        raise AttributeError(item)


class _FakePatch:
    def __init__(self):
        self.status = {}


FAKE_OWNER = {
    "apiVersion": "octonaut.rocks/v1alpha1",
    "kind": "TradingAgent",
    "metadata": {"name": "minisaurus", "namespace": "default", "uid": "fake-uid"},
}


def test_reconcile_applies_configmap_deployment_service_and_default_postgres():
    api = _RecordingApi()
    clients = Clients(core_v1=api, apps_v1=api, networking_v1=api)
    patch = _FakePatch()

    reconcile_tradingagent(MINIMAL_SPEC, "minisaurus", "default", patch, clients=clients,
                             owner=FAKE_OWNER)

    kinds = {k for k, _ in api.created}
    assert "config_map" in kinds
    assert "deployment" in kinds  # both the agent and the default postgres deployment
    assert "service" in kinds
    assert "secret" in kinds  # default postgres secret
    assert "persistent_volume_claim" in kinds
    assert patch.status["phase"] == "Running"


def test_reconcile_skips_ingress_when_not_configured():
    api = _RecordingApi()
    clients = Clients(core_v1=api, apps_v1=api, networking_v1=api)
    patch = _FakePatch()

    reconcile_tradingagent(MINIMAL_SPEC, "minisaurus", "default", patch, clients=clients,
                             owner=FAKE_OWNER)

    assert "ingress" not in {k for k, _ in api.created}


def test_reconcile_applies_ingress_when_configured():
    spec = {**MINIMAL_SPEC, "ingress": {"className": "traefik", "host": "minisaurus.localhost"}}
    api = _RecordingApi()
    clients = Clients(core_v1=api, apps_v1=api, networking_v1=api)
    patch = _FakePatch()

    reconcile_tradingagent(spec, "minisaurus", "default", patch, clients=clients, owner=FAKE_OWNER)

    assert "ingress" in {k for k, _ in api.created}


def test_reconcile_skips_default_postgres_when_user_supplied():
    spec = {**MINIMAL_SPEC, "postgres": {"databaseUrl": {"secretKeyRef":
             {"name": "my-db", "key": "url"}}}}
    api = _RecordingApi()
    clients = Clients(core_v1=api, apps_v1=api, networking_v1=api)
    patch = _FakePatch()

    reconcile_tradingagent(spec, "minisaurus", "default", patch, clients=clients, owner=FAKE_OWNER)

    assert "persistent_volume_claim" not in {k for k, _ in api.created}
    assert ("secret", "my-db") not in api.created
