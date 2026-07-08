import yaml

from krakosaurus_operator.resources import (
    build_configmap,
    build_deployment,
    build_ingress,
    build_service,
)

MINIMAL_SPEC = {
    "openrouter": {
        "model": "poolside/laguna-m.1",
        "apiKey": {"secretKeyRef": {"name": "minisaurus-secret", "key": "openrouter-key"}},
    },
    "strategy": {
        "type": "GRID", "ticker": "BTCUSD", "balance": 50000,
        "prompt": "Trade BTC/USD conservatively.\n",
    },
}

DB_SECRET_REF = {"name": "minisaurus-db", "key": "DATABASE_URL"}


def test_configmap_renders_strategy_and_fixed_logging_default():
    cm = build_configmap("minisaurus", "default", MINIMAL_SPEC)
    assert cm["kind"] == "ConfigMap"
    assert cm["metadata"]["name"] == "minisaurus-config"
    assert cm["metadata"]["namespace"] == "default"

    config = yaml.safe_load(cm["data"]["config.yaml"])
    assert config["strategy"]["type"] == "GRID"
    assert config["strategy"]["ticker"] == "BTCUSD"
    assert config["strategy"]["balance"] == 50000
    assert config["logging"] == {"level": "INFO", "format": "json"}


def test_deployment_wires_openrouter_and_database_env():
    dep = build_deployment("minisaurus", "default", MINIMAL_SPEC, image="agent:dev",
                            database_url_secret_ref=DB_SECRET_REF)
    container = dep["spec"]["template"]["spec"]["containers"][0]
    env_by_name = {e["name"]: e for e in container["env"]}

    assert env_by_name["OPENROUTER_MODEL"]["value"] == "poolside/laguna-m.1"
    assert env_by_name["OPENROUTER_API_KEY"]["valueFrom"]["secretKeyRef"] == {
        "name": "minisaurus-secret", "key": "openrouter-key",
    }
    assert env_by_name["DATABASE_URL"]["valueFrom"]["secretKeyRef"] == DB_SECRET_REF
    assert container["image"] == "agent:dev"
    assert dep["metadata"]["name"] == "minisaurus"
    assert dep["spec"]["template"]["spec"]["volumes"][0]["configMap"]["name"] == "minisaurus-config"


def test_deployment_omits_langfuse_env_when_not_configured():
    dep = build_deployment("minisaurus", "default", MINIMAL_SPEC, image="agent:dev",
                            database_url_secret_ref=DB_SECRET_REF)
    container = dep["spec"]["template"]["spec"]["containers"][0]
    names = {e["name"] for e in container["env"]}
    assert "LANGFUSE_ADDRESS" not in names
    assert "LANGFUSE_PUBLIC_KEY" not in names
    assert "LANGFUSE_SECRET_KEY" not in names


def test_deployment_wires_langfuse_env_when_configured():
    spec = {
        **MINIMAL_SPEC,
        "langfuse": {
            "address": "http://langfuse-web.langfuse.svc.cluster.local:3000",
            "publicKey": {"secretKeyRef": {"name": "lf-secret", "key": "public-key"}},
            "secretKey": {"secretKeyRef": {"name": "lf-secret", "key": "secret-key"}},
        },
    }
    dep = build_deployment("minisaurus", "default", spec, image="agent:dev",
                            database_url_secret_ref=DB_SECRET_REF)
    container = dep["spec"]["template"]["spec"]["containers"][0]
    env_by_name = {e["name"]: e for e in container["env"]}

    assert env_by_name["LANGFUSE_ADDRESS"]["value"] == \
        "http://langfuse-web.langfuse.svc.cluster.local:3000"
    assert env_by_name["LANGFUSE_PUBLIC_KEY"]["valueFrom"]["secretKeyRef"] == {
        "name": "lf-secret", "key": "public-key",
    }
    assert env_by_name["LANGFUSE_SECRET_KEY"]["valueFrom"]["secretKeyRef"] == {
        "name": "lf-secret", "key": "secret-key",
    }


def test_deployment_omits_resources_when_not_configured():
    dep = build_deployment("minisaurus", "default", MINIMAL_SPEC, image="agent:dev",
                            database_url_secret_ref=DB_SECRET_REF)
    container = dep["spec"]["template"]["spec"]["containers"][0]
    assert "resources" not in container


def test_deployment_sets_resources_when_configured():
    spec = {**MINIMAL_SPEC, "resources": {"requests": {"cpu": "0.5", "memory": "512Mi"}}}
    dep = build_deployment("minisaurus", "default", spec, image="agent:dev",
                            database_url_secret_ref=DB_SECRET_REF)
    container = dep["spec"]["template"]["spec"]["containers"][0]
    assert container["resources"] == {"requests": {"cpu": "0.5", "memory": "512Mi"}}


def test_service_targets_the_agent_deployment_on_port_8000():
    svc = build_service("minisaurus", "default")
    assert svc["kind"] == "Service"
    assert svc["spec"]["selector"] == {"app": "minisaurus"}
    assert svc["spec"]["ports"][0]["port"] == 8000


def test_ingress_is_none_when_not_configured():
    assert build_ingress("minisaurus", "default", None) is None


def test_ingress_renders_when_configured():
    ingress_spec = {"className": "traefik", "host": "minisaurus.localhost", "path": "/"}
    ing = build_ingress("minisaurus", "default", ingress_spec)
    assert ing["kind"] == "Ingress"
    assert ing["spec"]["ingressClassName"] == "traefik"
    rule = ing["spec"]["rules"][0]
    assert rule["host"] == "minisaurus.localhost"
    backend = rule["http"]["paths"][0]["backend"]["service"]
    assert backend["name"] == "minisaurus"
    assert backend["port"]["number"] == 8000
