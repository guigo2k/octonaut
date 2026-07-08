"""kopf reconcile handlers for the ``TradingAgent`` CRD.

Structured as a thin ``@kopf.on.*``-decorated wrapper around
``reconcile_tradingagent``, which takes an injected ``Clients`` bundle -- so
the full reconcile orchestration is unit-testable against fake API objects,
with no real cluster required. Owner references (``kopf.adopt``) make every
created object cluster-GC'd when the CR is deleted; no finalizer needed.
"""

import base64
import logging
import os
from dataclasses import dataclass

import kopf
from kubernetes import client, config as k8s_config
from kubernetes.client.exceptions import ApiException

from octonaut_operator.postgres import build_default_postgres, build_postgres_network_policy
from octonaut_operator.resources import (
    build_configmap,
    build_deployment,
    build_ingress,
    build_network_policy,
    build_service,
)

logger = logging.getLogger(__name__)

AGENT_IMAGE = os.environ.get("AGENT_IMAGE", "octonaut-agent:dev")

# Off by default (dev: open networking). Set true in clusters that want a
# default-deny NetworkPolicy per agent, with only DNS/HTTPS/its own
# Postgres/Langfuse/ingress punched through -- see resources.build_network_policy.
NETWORK_POLICY_ENABLED = os.environ.get("NETWORK_POLICY_ENABLED", "false").lower() == "true"


@dataclass
class Clients:
    core_v1: object
    apps_v1: object
    networking_v1: object


def _real_clients() -> Clients:
    k8s_config.load_incluster_config()
    return Clients(core_v1=client.CoreV1Api(), apps_v1=client.AppsV1Api(),
                    networking_v1=client.NetworkingV1Api())


def resolve_database_ref(
    spec: dict, name: str, namespace: str, *, existing_password: str | None
) -> tuple[dict, dict | None]:
    """Decide the agent's ``DATABASE_URL`` secretKeyRef, and any default
    Postgres+pgvector resources that need to be applied alongside it.

    ``spec.postgres`` present -> passthrough, nothing extra to provision.
    Omitted -> provision a default instance, reusing ``existing_password`` on
    update reconciles so the live DB connection never breaks.
    """
    postgres_spec = spec.get("postgres")
    if postgres_spec:
        return postgres_spec["databaseUrl"]["secretKeyRef"], None
    pg = build_default_postgres(name, namespace, password=existing_password)
    return pg["database_url_secret_ref"], pg


def _read_existing_password(core_v1, secret_name: str, namespace: str) -> str | None:
    try:
        secret = core_v1.read_namespaced_secret(secret_name, namespace)
    except ApiException as exc:
        if exc.status == 404:
            return None
        raise
    return base64.b64decode(secret.data["POSTGRES_PASSWORD"]).decode()


def _apply(create_fn, patch_fn, name: str, namespace: str, body: dict) -> None:
    """Idempotent apply: create, or patch if it already exists (409)."""
    try:
        create_fn(namespace, body)
    except ApiException as exc:
        if exc.status != 409:
            raise
        patch_fn(name, namespace, body)


def reconcile_tradingagent(
    spec: dict, name: str, namespace: str, patch, *, clients: Clients, owner: dict
) -> None:
    core_v1, apps_v1, networking_v1 = clients.core_v1, clients.apps_v1, clients.networking_v1

    existing_password = _read_existing_password(core_v1, f"{name}-db", namespace)
    database_url_secret_ref, pg = resolve_database_ref(
        spec, name, namespace, existing_password=existing_password
    )

    configmap = build_configmap(name, namespace, spec)
    deployment = build_deployment(name, namespace, spec, image=spec.get("image", AGENT_IMAGE),
                                    database_url_secret_ref=database_url_secret_ref)
    service = build_service(name, namespace)
    ingress = build_ingress(name, namespace, spec.get("ingress"))

    network_policy = None
    if NETWORK_POLICY_ENABLED:
        db_pod_labels = {"app": f"{name}-postgres"} if pg is not None else None
        network_policy = build_network_policy(name, namespace, spec, db_pod_labels=db_pod_labels)

    for obj in (configmap, deployment, service, *([ingress] if ingress else []),
                *([network_policy] if network_policy else [])):
        kopf.adopt(obj, owner=owner)

    _apply(core_v1.create_namespaced_config_map, core_v1.patch_namespaced_config_map,
           configmap["metadata"]["name"], namespace, configmap)
    _apply(apps_v1.create_namespaced_deployment, apps_v1.patch_namespaced_deployment,
           deployment["metadata"]["name"], namespace, deployment)
    _apply(core_v1.create_namespaced_service, core_v1.patch_namespaced_service,
           service["metadata"]["name"], namespace, service)
    if ingress:
        _apply(networking_v1.create_namespaced_ingress, networking_v1.patch_namespaced_ingress,
               ingress["metadata"]["name"], namespace, ingress)
    if network_policy:
        _apply(networking_v1.create_namespaced_network_policy,
               networking_v1.patch_namespaced_network_policy,
               network_policy["metadata"]["name"], namespace, network_policy)

    if pg is not None:
        pg_network_policy = build_postgres_network_policy(name, namespace) \
            if NETWORK_POLICY_ENABLED else None
        for obj in (pg["secret"], pg["pvc"], pg["deployment"], pg["service"],
                    *([pg_network_policy] if pg_network_policy else [])):
            kopf.adopt(obj, owner=owner)
        _apply(core_v1.create_namespaced_secret, core_v1.patch_namespaced_secret,
               pg["secret"]["metadata"]["name"], namespace, pg["secret"])
        _apply(core_v1.create_namespaced_persistent_volume_claim,
               core_v1.patch_namespaced_persistent_volume_claim,
               pg["pvc"]["metadata"]["name"], namespace, pg["pvc"])
        _apply(apps_v1.create_namespaced_deployment, apps_v1.patch_namespaced_deployment,
               pg["deployment"]["metadata"]["name"], namespace, pg["deployment"])
        _apply(core_v1.create_namespaced_service, core_v1.patch_namespaced_service,
               pg["service"]["metadata"]["name"], namespace, pg["service"])
        if pg_network_policy:
            _apply(networking_v1.create_namespaced_network_policy,
                   networking_v1.patch_namespaced_network_policy,
                   pg_network_policy["metadata"]["name"], namespace, pg_network_policy)

    patch.status["phase"] = "Running"


@kopf.on.create("octonaut.rocks", "v1alpha1", "tradingagents")
@kopf.on.update("octonaut.rocks", "v1alpha1", "tradingagents")
@kopf.on.resume("octonaut.rocks", "v1alpha1", "tradingagents")
def on_reconcile(spec, name, namespace, patch, body, **_):
    reconcile_tradingagent(spec, name, namespace, patch, clients=_real_clients(), owner=body)
