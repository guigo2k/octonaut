from octonaut_operator.postgres import build_default_postgres


def test_database_url_points_at_the_provisioned_service():
    result = build_default_postgres("minisaurus", "default", password="hunter2")
    url = result["secret"]["stringData"]["DATABASE_URL"]
    assert "minisaurus-postgres.default.svc.cluster.local" in url
    assert "hunter2" in url
    assert url.startswith("postgresql+psycopg://")


def test_database_url_secret_ref_points_at_the_created_secret():
    result = build_default_postgres("minisaurus", "default", password="hunter2")
    assert result["database_url_secret_ref"] == {"name": "minisaurus-db", "key": "DATABASE_URL"}
    assert result["secret"]["metadata"]["name"] == "minisaurus-db"


def test_deployment_uses_pgvector_image():
    result = build_default_postgres("minisaurus", "default", password="hunter2")
    container = result["deployment"]["spec"]["template"]["spec"]["containers"][0]
    assert container["image"] == "pgvector/pgvector:pg17"


def test_deployment_references_the_generated_secret_for_its_password():
    result = build_default_postgres("minisaurus", "default", password="hunter2")
    container = result["deployment"]["spec"]["template"]["spec"]["containers"][0]
    pw_env = next(e for e in container["env"] if e["name"] == "POSTGRES_PASSWORD")
    assert pw_env["valueFrom"]["secretKeyRef"] == {"name": "minisaurus-db", "key": "POSTGRES_PASSWORD"}


def test_password_is_randomly_generated_when_not_provided():
    a = build_default_postgres("minisaurus", "default")
    b = build_default_postgres("minisaurus", "default")
    assert a["secret"]["stringData"]["POSTGRES_PASSWORD"] != \
        b["secret"]["stringData"]["POSTGRES_PASSWORD"]


def test_password_can_be_pinned_for_stable_reconciles():
    # handlers.py must reuse an existing secret's password across reconciles
    # rather than regenerating it (which would break the live DB connection).
    result = build_default_postgres("minisaurus", "default", password="fixed-pw")
    assert result["secret"]["stringData"]["POSTGRES_PASSWORD"] == "fixed-pw"
    assert "fixed-pw" in result["secret"]["stringData"]["DATABASE_URL"]


def test_pvc_and_service_are_named_after_the_postgres_deployment():
    result = build_default_postgres("minisaurus", "default", password="hunter2")
    assert result["pvc"]["metadata"]["name"] == "minisaurus-postgres"
    assert result["service"]["metadata"]["name"] == "minisaurus-postgres"
    assert result["service"]["spec"]["selector"] == {"app": "minisaurus-postgres"}
