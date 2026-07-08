"""Pure builder functions: TradingAgent CRD spec -> Kubernetes resource dicts.

No Kubernetes API calls here -- ``handlers.py`` applies what these return.
Keeping them pure makes the CRD-field -> resource-field mapping directly
unit-testable without a cluster.
"""

from urllib.parse import urlparse

import yaml

_PORT = 8000
_FIXED_LOGGING = {"level": "INFO", "format": "json"}
_DNS_PORT = 53
_HTTPS_PORT = 443
_POSTGRES_PORT = 5432
_KUBE_SYSTEM_NAMESPACE_SELECTOR = {
    "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "kube-system"}}
}


def build_configmap(name: str, namespace: str, spec: dict) -> dict:
    config_yaml = yaml.safe_dump(
        {"strategy": spec["strategy"], "logging": _FIXED_LOGGING}, sort_keys=False
    )
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": f"{name}-config", "namespace": namespace},
        "data": {"config.yaml": config_yaml},
    }


def _secret_env(env_name: str, secret_key_ref: dict) -> dict:
    return {"name": env_name, "valueFrom": {"secretKeyRef": secret_key_ref}}


def build_deployment(
    name: str, namespace: str, spec: dict, *, image: str, database_url_secret_ref: dict
) -> dict:
    env = [
        {"name": "OPENROUTER_MODEL", "value": spec["openrouter"]["model"]},
        _secret_env("OPENROUTER_API_KEY", spec["openrouter"]["apiKey"]["secretKeyRef"]),
        {"name": "AGENT_CONFIG", "value": "/etc/agent/config.yaml"},
        _secret_env("DATABASE_URL", database_url_secret_ref),
    ]

    langfuse = spec.get("langfuse")
    if langfuse:
        env.append({"name": "LANGFUSE_HOST", "value": langfuse["address"]})
        env.append(_secret_env("LANGFUSE_PUBLIC_KEY", langfuse["publicKey"]["secretKeyRef"]))
        env.append(_secret_env("LANGFUSE_SECRET_KEY", langfuse["secretKey"]["secretKeyRef"]))

    container = {
        "name": "agent",
        "image": image,
        "ports": [{"name": "http", "containerPort": _PORT}],
        "env": env,
        "volumeMounts": [{"name": "config", "mountPath": "/etc/agent", "readOnly": True}],
        "livenessProbe": {"httpGet": {"path": "/health", "port": "http"},
                            "initialDelaySeconds": 5, "periodSeconds": 10},
        "readinessProbe": {"httpGet": {"path": "/health", "port": "http"},
                             "initialDelaySeconds": 5, "periodSeconds": 10},
    }
    if "resources" in spec:
        container["resources"] = spec["resources"]

    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": name, "namespace": namespace},
        "spec": {
            "replicas": 1,
            "selector": {"matchLabels": {"app": name}},
            "template": {
                "metadata": {"labels": {"app": name}},
                "spec": {
                    # Gives the SIGTERM handler (cancel all open paper
                    # orders) time to finish before Kubernetes SIGKILLs.
                    "terminationGracePeriodSeconds": 30,
                    "containers": [container],
                    "volumes": [{"name": "config", "configMap": {"name": f"{name}-config"}}],
                },
            },
        },
    }


def build_service(name: str, namespace: str) -> dict:
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": name, "namespace": namespace},
        "spec": {
            "selector": {"app": name},
            "ports": [{"name": "http", "port": _PORT, "targetPort": "http"}],
        },
    }


def build_ingress(name: str, namespace: str, ingress_spec: dict | None) -> dict | None:
    if not ingress_spec:
        return None
    ing = {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "Ingress",
        "metadata": {"name": name, "namespace": namespace},
        "spec": {
            "ingressClassName": ingress_spec["className"],
            "rules": [{
                "host": ingress_spec["host"],
                "http": {"paths": [{
                    "path": ingress_spec.get("path", "/"),
                    "pathType": "Prefix",
                    "backend": {"service": {"name": name, "port": {"number": _PORT}}},
                }]},
            }],
        },
    }
    if ingress_spec.get("tls"):
        ing["spec"]["tls"] = ingress_spec["tls"]
    return ing


def _langfuse_peer(address: str) -> tuple[dict | None, int]:
    """Best-effort (namespaceSelector, port) for the Langfuse egress rule.

    Namespace is derived from the in-cluster FQDN convention
    (``<service>.<namespace>.svc.cluster.local``) -- the CRD only carries a
    URL, not a structured namespace, and cross-namespace NetworkPolicy peers
    require a namespaceSelector. Falls back to an unscoped (port-only) rule
    if the address doesn't look like an in-cluster FQDN.
    """
    parsed = urlparse(address)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    labels = (parsed.hostname or "").split(".")
    if len(labels) > 1 and labels[-1] == "local" and "svc" in labels:
        return {"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": labels[1]}}}, port
    return None, port


def build_network_policy(
    name: str, namespace: str, spec: dict, *, db_pod_labels: dict | None
) -> dict:
    """Default-deny NetworkPolicy for the agent pod.

    Only what the agent actually needs gets an explicit hole punched:
    DNS, outbound HTTPS (OpenRouter + the Kraken CLI subprocess -- both
    reached at addresses the operator doesn't control, so this can only be
    scoped by port), its own Postgres, optionally Langfuse, and -- only if
    ``spec.ingress`` is set -- inbound from the ingress controller's
    namespace (``kube-system``, where this project's Traefik runs).
    """
    egress = [
        {
            "to": [_KUBE_SYSTEM_NAMESPACE_SELECTOR],
            "ports": [{"protocol": "UDP", "port": _DNS_PORT}, {"protocol": "TCP", "port": _DNS_PORT}],
        },
        {"ports": [{"protocol": "TCP", "port": _HTTPS_PORT}]},
    ]

    if db_pod_labels is not None:
        egress.append({
            "to": [{"podSelector": {"matchLabels": db_pod_labels}}],
            "ports": [{"protocol": "TCP", "port": _POSTGRES_PORT}],
        })
    elif spec.get("postgres"):
        # User-supplied Postgres: its address isn't known to the operator.
        egress.append({"ports": [{"protocol": "TCP", "port": _POSTGRES_PORT}]})

    langfuse = spec.get("langfuse")
    if langfuse:
        peer, port = _langfuse_peer(langfuse["address"])
        rule = {"ports": [{"protocol": "TCP", "port": port}]}
        if peer:
            rule["to"] = [peer]
        egress.append(rule)

    ingress = []
    if spec.get("ingress"):
        ingress.append({
            "from": [_KUBE_SYSTEM_NAMESPACE_SELECTOR],
            "ports": [{"protocol": "TCP", "port": _PORT}],
        })

    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {"name": name, "namespace": namespace},
        "spec": {
            "podSelector": {"matchLabels": {"app": name}},
            "policyTypes": ["Ingress", "Egress"],
            "ingress": ingress,
            "egress": egress,
        },
    }
