"""Default in-cluster Postgres+pgvector, provisioned when ``spec.postgres`` is
omitted from the ``TradingAgent`` CR (the sample ``minisaurus`` CR in the task
omits it entirely, so this is required for that example to actually deploy).

Same posture as the reference agent's "reuse a small bundled Postgres"
default: single replica, small PVC, not production-sized.
"""

import secrets
import string

_PG_PORT = 5432
_PG_IMAGE = "pgvector/pgvector:pg17"
_PG_USER = "agent"
_PG_DB = "agent"


def _generate_password(length: int = 24) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def build_default_postgres(name: str, namespace: str, *, password: str | None = None) -> dict:
    """Returns the Secret/PVC/Deployment/Service for a default Postgres+pgvector
    instance, plus the ``secretKeyRef`` the agent Deployment should use for
    ``DATABASE_URL``.

    ``password`` should be the *existing* secret's password on update
    reconciles (read back by the caller) -- regenerating it on every
    reconcile would break the live DB connection.
    """
    pw = password or _generate_password()
    pg_name = f"{name}-postgres"
    secret_name = f"{name}-db"
    host = f"{pg_name}.{namespace}.svc.cluster.local"
    database_url = f"postgresql+psycopg://{_PG_USER}:{pw}@{host}:{_PG_PORT}/{_PG_DB}"

    secret = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": secret_name, "namespace": namespace},
        "stringData": {"DATABASE_URL": database_url, "POSTGRES_PASSWORD": pw},
    }

    pvc = {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": {"name": pg_name, "namespace": namespace},
        "spec": {"accessModes": ["ReadWriteOnce"],
                  "resources": {"requests": {"storage": "1Gi"}}},
    }

    deployment = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": pg_name, "namespace": namespace},
        "spec": {
            "replicas": 1,
            "selector": {"matchLabels": {"app": pg_name}},
            "template": {
                "metadata": {"labels": {"app": pg_name}},
                "spec": {
                    "containers": [{
                        "name": "postgres",
                        "image": _PG_IMAGE,
                        "ports": [{"name": "postgres", "containerPort": _PG_PORT}],
                        "env": [
                            {"name": "POSTGRES_USER", "value": _PG_USER},
                            {"name": "POSTGRES_DB", "value": _PG_DB},
                            {"name": "POSTGRES_PASSWORD",
                              "valueFrom": {"secretKeyRef": {"name": secret_name,
                                                                "key": "POSTGRES_PASSWORD"}}},
                        ],
                        "volumeMounts": [{"name": "data",
                                            "mountPath": "/var/lib/postgresql/data",
                                            "subPath": "pgdata"}],
                    }],
                    "volumes": [{"name": "data",
                                  "persistentVolumeClaim": {"claimName": pg_name}}],
                },
            },
        },
    }

    service = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": pg_name, "namespace": namespace},
        "spec": {"selector": {"app": pg_name}, "ports": [{"name": "postgres", "port": _PG_PORT}]},
    }

    return {
        "secret": secret,
        "pvc": pvc,
        "deployment": deployment,
        "service": service,
        "database_url_secret_ref": {"name": secret_name, "key": "DATABASE_URL"},
    }
