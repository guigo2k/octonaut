"""Pure builder functions: TradingAgent CRD spec -> Kubernetes resource dicts.

No Kubernetes API calls here -- ``handlers.py`` applies what these return.
Keeping them pure makes the CRD-field -> resource-field mapping directly
unit-testable without a cluster.
"""

import yaml

_PORT = 8000
_FIXED_LOGGING = {"level": "INFO", "format": "json"}


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
