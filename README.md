# Octonaut

A minimal AI trading agent (`agent/`) and the Kubernetes operator that
deploys it (`operator/`). Fictional use case: a single always-on agent that
trades one ticker under one strategy (DCA / GRID / TWAP) in Kraken's paper
mode, reasoning with an LLM over live market data and its own trade memory,
never placing an order the LLM proposes without a deterministic solvency
check in between.

Two independent, separately-deployable pieces:

- **`agent/`** — the trading agent itself. Runs standalone with three env
  vars; no Kubernetes required.
- **`operator/`** — a [kopf](https://kopf.readthedocs.io/)-based operator
  that reconciles a `TradingAgent` custom resource into a running agent
  (Deployment, Service, ConfigMap, optional Ingress, and a default
  Postgres+pgvector instance if you don't bring your own).

## Architecture

```
┌─────────────────── agent pod ───────────────────┐
│ FastAPI (health/trades/positions/metrics)        │
│  + APScheduler tick -> runner.run_once           │
│                                                   │
│  runner.run_once:                                │
│   ensure_paper -> load_skills (deterministic:    │
│   Core + Market Data + strategy-type skill)      │
│   -> recall trade memory (pgvector, semantic)    │
│   -> graph.build_graph (LangGraph):              │
│        gather (kraken ticker/status/balance)     │
│        -> reason (OpenRouter LLM, ReAct agent    │
│           w/ read-only ticker/ohlc tools)         │
│        -> solvency guard (deterministic, no LLM) │
│        -> execute (kraken paper buy/sell)         │
│   -> persist Trade (ledger) + TradeMemory (RAG)  │
└──────────┬────────────────────────────────────────┘
           │ DATABASE_URL (postgres+pgvector: ledger + trade memory)
           │ OPENROUTER_* (LLM)
           │ LANGFUSE_* (optional: traces)
           ▼
     kraken CLI (subprocess, paper mode) · Postgres
```

**Safety invariant:** the order-placement tool is only ever called by the
deterministic `execute` step, never by the LLM. The LLM can propose a trade
(action/size/rationale); a pure `solvency_guard` function (balance vs. cost,
held size vs. sell size) is the sole gate before anything is placed. "Never
use leverage" is enforced structurally — no leverage/margin tool exists for
the LLM to call, spot paper trading only.

## Bootstrapping a local cluster

`octonaut.yaml` is a self-contained Lima config — a fresh k3s + ArgoCD stack,
independent of any other cluster. No manual image loading: two systemd units
(`build-agent`/`build-operator`) watch `agent/` and `operator/` and
rebuild+import each image on every save.

```bash
limactl start --name octonaut --param pwd=$(pwd) ./octonaut.yaml
```

Give it a couple of minutes for k3s, ArgoCD, and the first image builds to
settle, then:

```bash
export KUBECONFIG=$(limactl list octonaut --format 'unix://{{.Dir}}/copied-from-guest/kubeconfig.yaml')
kubectl get application -n argocd   # all should show Synced/Healthy
```

Three UIs come up on `*.localhost` (Traefik ingress, no `/etc/hosts` edits
needed on macOS): `argocd.localhost`, `coroot.localhost`, `langfuse.localhost`.
ArgoCD's admin password:

```bash
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" | base64 -d
```

Only the operator itself is ArgoCD-managed (`clusters/dev/apps/
octonaut-operator.yaml`); `TradingAgent` CRs are applied by hand — see
"Deploying the example agents" below.

### Setting up Langfuse

Fully automated — no signup, no clicking through Settings → API Keys.
`scripts/generate-langfuse-secrets` runs during provisioning (before ArgoCD
syncs anything) and creates one `langfuse-generated-secrets` Secret in the
`langfuse` namespace holding random passwords for every backing service
*and* a ready-made org/project/user/API-key pair, via Langfuse's
[headless initialization](https://langfuse.com/self-hosting/administration/headless-initialization)
(`LANGFUSE_INIT_*` env vars, wired in via `clusters/dev/apps/langfuse.yaml`'s
`langfuse.additionalEnv`). `langfuse.yaml` has no literal credentials left in
it at all — every value is `secretKeyRef`/`existingSecret` against that one
Secret. It's idempotent: re-running the script on an existing cluster is a
no-op if the Secret is already there.

To retrieve the generated login (to use the UI) or API keys (to point the
agent's `LANGFUSE_*` env vars / a `TradingAgent`'s `spec.langfuse` at):

```bash
kubectl get secret langfuse-generated-secrets -n langfuse -o jsonpath='{.data.init-user-email}' | base64 -d; echo
kubectl get secret langfuse-generated-secrets -n langfuse -o jsonpath='{.data.init-user-password}' | base64 -d; echo
kubectl get secret langfuse-generated-secrets -n langfuse -o jsonpath='{.data.init-project-public-key}' | base64 -d; echo
kubectl get secret langfuse-generated-secrets -n langfuse -o jsonpath='{.data.init-project-secret-key}' | base64 -d; echo
```

Note: the Secret is only generated once per cluster (on a namespace that
doesn't already have it) — headless init itself is also first-boot-only, so
deleting and recreating the Secret against an already-initialized Postgres
volume won't retroactively create a second org/project.

### Deploying the example agents

All three `examples/*.yaml` CRs deploy into the `default` namespace and
share one secret. `openrouter-key` is the one credential that's genuinely
external (bring your own [OpenRouter](https://openrouter.ai/) key); the two
Langfuse values come straight out of the generated secret above:

```bash
kubectl create secret generic octonaut-secret \
  --from-literal=openrouter-key=sk-or-... \
  --from-literal=langfuse-public-key="$(kubectl get secret langfuse-generated-secrets -n langfuse -o jsonpath='{.data.init-project-public-key}' | base64 -d)" \
  --from-literal=langfuse-secret-key="$(kubectl get secret langfuse-generated-secrets -n langfuse -o jsonpath='{.data.init-project-secret-key}' | base64 -d)" \
  -n default

kubectl apply -f examples/
```

Each provisions its own default Postgres+pgvector (none of the examples set
`spec.postgres`). Watch them come up:

```bash
kubectl get tradingagent -n default
kubectl get pods -n default
```

## Running the agent standalone

Requires the `kraken` CLI on `PATH` ([install](https://github.com/krakenfx/kraken-cli)) and a reachable Postgres with the `vector` extension available (`pgvector/pgvector:pg17` works).

Required env vars:

| Var | Purpose |
|---|---|
| `OPENROUTER_MODEL` | Model id passed to OpenRouter, e.g. `poolside/laguna-m.1` |
| `OPENROUTER_API_KEY` | Your [OpenRouter](https://openrouter.ai/) API key |
| `DATABASE_URL` | `postgresql+psycopg://user:pass@host:5432/db` |

Optional (all three must be set together to enable Langfuse tracing):

| Var | Purpose |
|---|---|
| `LANGFUSE_ADDRESS` | Langfuse instance base URL |
| `LANGFUSE_PUBLIC_KEY` | Langfuse public key |
| `LANGFUSE_SECRET_KEY` | Langfuse secret key |

Also: `AGENT_CONFIG` (default `/etc/agent/config.yaml`), `AGENT_TICK_SECONDS`
(default `300`).

`config.yaml`:

```yaml
strategy:
  type: GRID       # DCA | GRID | TWAP
  ticker: BTCUSD
  balance: 50000   # starting paper balance, passed to `kraken paper init --balance`
  prompt: |
    Trade BTC/USD conservatively.
    Require strong confirmation before entering.
    Never use leverage.
logging:
  level: INFO
  format: json      # or text
```

```bash
cd agent
uv sync
AGENT_CONFIG=./config.yaml \
DATABASE_URL=postgresql+psycopg://postgres:pw@localhost:55433/postgres \
OPENROUTER_MODEL=poolside/laguna-m.1 \
OPENROUTER_API_KEY=sk-... \
uv run python -m agent.main
```

`GET /health`, `GET /trades`, `GET /positions`, `GET /metrics` on `:8000`.
`SIGTERM` (e.g. `kubectl delete pod`, or Ctrl-C) cancels every open paper
order before the process exits.

## Running the operator on an existing cluster

Not using `octonaut.yaml`? Install the operator by hand instead (build/push
`octonaut-agent`/`octonaut-operator` images to wherever your cluster can
pull from first):

```bash
kubectl apply -f operator/deploy/crd.yaml
kubectl apply -f operator/deploy/rbac.yaml
kubectl apply -f operator/deploy/operator-deployment.yaml   # set AGENT_IMAGE first
```

Then follow "Setting up Langfuse" / "Deploying the example agents" above —
`examples/` has three ready-to-adapt CRs, one per strategy type and risk
posture, all in the `default` namespace:

| Example | Strategy | Ticker | Posture |
|---|---|---|---|
| `conservative-reptilian.yaml` | DCA | BTCUSD | small, regular buys; skip rather than chase |
| `moderate-pleiadian.yaml` | GRID | ETHUSD | grid around current price, moderate drawdown tolerance |
| `aggressive-grey.yaml` | TWAP | SOLUSD | momentum-aware execution, tolerates overpaying into a spike |

The CRD's `ingress` / `resources` / `langfuse` / `postgres` blocks are all
optional (see the commented-out examples in each file). Leaving `postgres`
unset makes the operator provision its own small Postgres+pgvector
`Deployment`/`PVC`/`Service`/`Secret` for you — required for these examples to
deploy anything at all, since none of them set `postgres`.

## Testing

```bash
cd agent && uv run pytest        # 61 tests; DB-backed tests need a reachable
                                    # Postgres+pgvector (TEST_DATABASE_URL env,
                                    # defaults to localhost:55433) or they skip
cd operator && uv run pytest     # 29 tests; all pure/fake-client, no cluster
```

## Live verification (Lima `krak`, kraktopus's existing k3s cluster)

Deployed for real: operator installed via `kubectl apply`, images built with
`buildah` inside the Lima VM and imported into k3s's containerd, sample
`TradingAgent` reconciled into a running agent + default Postgres+pgvector.
Confirmed live:

- `/health` `/trades` `/positions` `/metrics` all serve real data.
- A real OpenRouter LLM call (via `openai/gpt-4o-mini` — see model note below)
  reasoned over live `kraken ticker`/`ohlc` tool calls and correctly recalled
  an earlier trade from pgvector trade memory in its own rationale.
- A forced `buy` proposal round-tripped through the solvency guard into a
  real `kraken paper buy` order, visible in `/trades` and independently
  confirmed via `kraken paper status`.
- `SIGTERM` (pod delete) cancelled a real resting limit order before exit,
  confirmed via captured shutdown logs (`cancelled_order_ids`).
- Deleting the `TradingAgent` CR garbage-collected every owned resource
  (Deployments, Services, ConfigMap, default-Postgres Secret/PVC) via owner
  references, leaving only the independently-created `<name>-secret`.
- Real Langfuse traces are visible for live ticks, correctly tagged with a
  per-pod-lifetime `sessionId` and a per-strategy `userId` (see "Design
  decisions" below).

Three real bugs were found and fixed by this live pass (not caught by unit
tests, since they depend on the real CLI/SDK behavior):

1. **Langfuse was never actually wired into LLM calls.** `make_handler`/
   `current_trace_id` existed and were tested, but nothing attached the
   callback handler to the graph invocation — LangGraph only forwards
   `config` (callbacks/metadata) to node functions that declare a second
   `config` parameter. Fixed in `graph.make_reason_fn` + `runner.run_once`.
2. **`kraken paper orders`'s real shape** is `{"count", "mode",
   "open_orders": [...]}`, not a bare list — `close_all_open_orders` silently
   cancelled nothing against the real CLI. Fixed, with a regression test
   using the real shape.
3. **The paper account was only initialized lazily on the first scheduler
   tick** (up to `AGENT_TICK_SECONDS` after boot), not at startup — fixed by
   calling `ensure_paper` eagerly in `main()`.

A second live pass, after real traffic surfaced three more issues, found and
fixed:

4. **Uvicorn's own `uvicorn`/`uvicorn.access`/`uvicorn.error` loggers
   attached their own plain-text handlers**, bypassing `configure_logging`'s
   JSON formatter entirely — health-check access logs showed up as bare text
   lines interleaved with our structured ones. Fixed with
   `observability.uvicorn_log_config()`, passed to `uvicorn.run(...,
   log_config=...)`, which points those loggers back at the root logger.
5. **Langfuse's `Sessions`/`Users` views were empty** — nothing tagged a
   trace's `sessionId`/`userId`. Fixed by generating one session id per pod
   lifetime (`main.py`) and tagging every trace's user as `<ticker>-<type>`
   (`runner.run_once`), via the SDK's `langfuse_session_id`/`langfuse_user_id`
   metadata keys.
6. **Langfuse trace persistence itself** was previously blocked by a
   pre-existing gap in the shared Langfuse deployment's S3/MinIO credential
   wiring (`kraktopus/deploy/dev/apps/langfuse.yaml`) — fixed upstream in that
   repo (`s3.auth.rootUser`/`rootPassword`, see that repo's commit history);
   real traces now persist and are queryable via Langfuse's API/UI.

**Model note:** `poolside/laguna-m.1:free` (the example model in
`examples/*.yaml`) intermittently 502s at the upstream provider on
OpenRouter, specifically on tool-bound/structured-output requests (plain
chat completions succeed) — an external provider issue, not a code issue.
Live LLM reasoning was verified end-to-end with both `poolside/laguna-m.1:free`
and `openai/gpt-4o-mini`; swap `spec.openrouter.model` if the former is
misbehaving.

## Design decisions & simplifications

- **RAG is deterministic for skills, semantic only for trade memory.** Core +
  Market Data + the one strategy-type skill are a fixed lookup by
  `strategy.type` (there's nothing to embed — selection is already 1:1).
  pgvector is used only to recall past trade rationales by similarity, the
  one place free-text search actually helps.
- **Local embeddings** (`fastembed`, `BAAI/bge-small-en-v1.5`, baked into the
  Docker image at build time, `HF_HUB_OFFLINE=1` at runtime) rather than a
  second required API key — OpenRouter doesn't serve embeddings.
- **No deterministic risk-preset subsystem.** Nothing in the config or CRD
  schema calls for one; safety is structural (no leverage tool exists) plus
  one inline solvency check, not a configurable constraint matrix.
- **No Alembic.** One small schema, idempotent `CREATE TABLE IF NOT EXISTS`
  at startup (`agent.db.init_db`) instead of migration tooling.
- **One fixed tick interval**, not per-strategy cron — the config has no
  schedule field; the strategy-type skill + prompt encode DCA/GRID/TWAP
  *behavior*, not cadence.
- **kopf over Kubebuilder/client-go** for the operator — same language as the
  agent, smallest footprint for a "simple operator." The operator itself is
  ArgoCD-managed (`clusters/dev/apps/octonaut-operator.yaml`, plain-directory
  source over `operator/deploy/`); individual `TradingAgent` CRs are
  deliberately kept `kubectl apply`-only — GitOps for the platform, not for
  every trading strategy someone spins up.
- **Standard `networking.k8s.io/v1` Ingress**, not a Traefik-specific
  `IngressRoute` — the CRD's `ingress.className/host/path/tls` fields are
  generic, so the operator renders a portable resource.

## Known gaps / what I'd do with more time

- **Kraken CLI shapes.** Ticker/status/balance parsing (`agent/graph.py`) and
  the "list + cancel all open orders" SIGTERM path (`agent/kraken.py`) are
  built from the real CLI's documented examples, not exercised against a live
  paper account yet — first thing to confirm in live verification.
- **Reasoning quality** is observed via Langfuse traces, not unit-asserted —
  same tradeoff the reference agent made; a small eval harness would be a
  good next step.
- **Multi-strategy / multiple tickers per agent** — intentionally out of
  scope; the config and CRD are both single-strategy by design here.
- **GitOps for TradingAgent CRs.** Only the operator is ArgoCD-managed;
  individual CRs stay `kubectl apply`-only by design (see above) — an
  `ApplicationSet` per-CR would be the natural extension if that's wanted.
- **Operator status detail.** `status.phase` is set to `Running` once
  resources are applied, not once the agent Pod is actually healthy — a
  proper implementation would watch the child Deployment's rollout status.
