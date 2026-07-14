# TARS

A lightweight Python utility that continuously verifies
whether **Topology Aware Routing** (formerly *Topology Aware Hints*) is actually
active for a Kubernetes Service running on **OKD 4.13 / Kubernetes 1.26**.

A Service can be *annotated* for topology-aware routing, but the feature is only
truly **active** when the EndpointSlice controller has placed topology hints on
the ready endpoints. This monitor reads the live cluster state on a schedule and
reports that ground truth as structured JSON logs.

> Works with the older name **Topology Aware Hints** (1.26) and the renamed
> **Topology Aware Routing** (1.27+). The annotation and the EndpointSlice hints
> are identical across both.

---

## How "active" is decided

A check reports `status: ENABLED` (`tar_enabled: true`) **only** when **all**
of the following are true:

1. At least one **EndpointSlice** exists for the Service.
2. At least one endpoint is **ready**.
3. **Every** ready endpoint carries topology hints (`hints.forZones`).

Otherwise it reports `status: DISABLED`.

The hint-placement algorithm in the EndpointSlice controller is all-or-nothing
per slice, so requiring every ready endpoint to be hinted reflects whether TAR
is genuinely engaged rather than only partially applied.

`annotation_present` is reported independently: it records whether the Service
carries the `service.kubernetes.io/topology-mode` annotation — useful context,
but **not** proof that TAR is active.

---

## Project structure

```
.
├── app.py                 # Entry point: main loop, signal handling, logging
├── config.py              # Environment-based configuration + validation
├── logger.py              # INFO-only structured JSON logging
├── kubernetes_client.py   # In-cluster / kubeconfig client initialization
├── tar_checker.py         # TAR verification logic (the core)
├── requirements.txt
├── Dockerfile
├── README.md
├── chart/                  # Helm chart
│   ├── Chart.yaml
│   ├── values.yaml
│   └── templates/
└── manifests/
    ├── serviceaccount.yaml
    ├── role.yaml
    ├── rolebinding.yaml
    └── deployment.yaml
```

### Architecture

```
        ┌──────────────┐
 env ─► │   config.py  │  validates NAMESPACE / SERVICE_NAME / CHECK_INTERVAL
        └──────┬───────┘
               │ AppConfig
        ┌──────▼───────────────┐
        │  kubernetes_client.py│  in-cluster config  ──►  falls back to kubeconfig
        └──────┬───────────────┘
               │ CoreV1Api, DiscoveryV1Api
        ┌──────▼─────────┐    every CHECK_INTERVAL seconds
        │  tar_checker.py│────►  read Service
        │                │────►  list EndpointSlices (label kubernetes.io/service-name)
        │                │────►  count slices / endpoints / ready / hinted
        └──────┬─────────┘────►  evaluate ENABLED ruleset
               │ CheckResult
        ┌──────▼─────┐
        │  logger.py │  one JSON object per check on stdout
        └────────────┘
```

The design is intentionally small: configuration, Kubernetes client setup,
logging, and TAR evaluation are separated just enough to keep the code easy to
read and test.

---

## How TARS works

TARS runs as one pod in the same namespace as the Service you want to monitor.
On startup it loads Kubernetes credentials, reads its configuration from
environment variables, then loops forever:

1. Read the configured Service.
2. List EndpointSlices in the same namespace that belong to that Service.
3. Count EndpointSlices, total endpoints, ready endpoints, and hinted endpoints.
4. Decide whether TAR is enabled.
5. Emit one INFO JSON log record.
6. Sleep for `CHECK_INTERVAL` seconds and repeat.

### How TAR status is decided

TARS reports `tar_enabled: true` and `status: "ENABLED"` only when all of these
conditions are true:

1. At least one EndpointSlice exists for the Service.
2. At least one endpoint is ready.
3. Every ready endpoint has topology hints in `hints.forZones`.

If any condition is false, TARS reports `tar_enabled: false`,
`status: "DISABLED"`, and sets `disabled_reason`:

| Reason | Meaning |
|--------|---------|
| `no EndpointSlices found for Service` | Kubernetes has not created any EndpointSlices for the configured Service, or TARS cannot see matching slices. |
| `no ready endpoints found` | Matching EndpointSlices exist, but none of their endpoints are ready. |
| `not all ready endpoints have topology hints` | At least one ready endpoint is missing `hints.forZones`, so TAR is not fully active. |

The Service annotation `service.kubernetes.io/topology-mode` is reported as
`annotation_present`, but it is not used by itself to mark TAR enabled. The
annotation shows intent; EndpointSlice hints show whether routing hints were
actually placed.

### How the target Service is discovered

TARS reads two environment variables:

| Variable | Source | Purpose |
|----------|--------|---------|
| `NAMESPACE` | The Deployment sets this from the pod namespace using the Downward API. | Namespace containing the Service and EndpointSlices. |
| `SERVICE_NAME` | Helm value `serviceName`, or the static manifest env var. | Name of the Service to monitor. |

The checker calls `read_namespaced_service(name=SERVICE_NAME, namespace=NAMESPACE)`.
That validates the Service exists and lets TARS report whether the topology
mode annotation is present.

### How EndpointSlices are associated with the Service

Kubernetes labels EndpointSlices with their owning Service name:

```text
kubernetes.io/service-name=<service-name>
```

TARS lists EndpointSlices in `NAMESPACE` using this label selector:

```text
kubernetes.io/service-name=$SERVICE_NAME
```

It then inspects only those matching EndpointSlices.

### How often checks run

Checks run every `CHECK_INTERVAL` seconds. The default is `300` seconds. The
sleep is interruptible, so SIGTERM/SIGINT lets the pod stop cleanly.

### Kubernetes resources TARS reads

TARS only needs namespaced read access:

| Resource | API group | Verb | Why |
|----------|-----------|------|-----|
| `services` | core | `get` | Read the configured Service and its topology-mode annotation. |
| `endpointslices` | `discovery.k8s.io` | `list` | Find matching EndpointSlices and inspect endpoint readiness plus topology hints. |

It does not create, update, or delete cluster resources.

---

## Environment variables

| Variable        | Required | Default | Description                                            |
|-----------------|----------|---------|--------------------------------------------------------|
| `NAMESPACE`     | yes      | —       | Namespace of the Service to monitor.                   |
| `SERVICE_NAME`  | yes      | —       | Name of the Service to monitor.                        |
| `CHECK_INTERVAL`| no       | `300`   | Seconds between checks (positive integer).             |

---

## Local execution

Requires Python 3.12+ and a valid kubeconfig (`~/.kube/config`) pointing at a
cluster where you can read the target Service and its EndpointSlices.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export NAMESPACE=ingress-nginx
export SERVICE_NAME=nginx
export CHECK_INTERVAL=300

python3 app.py
```

Press `Ctrl+C` (SIGINT) or send `SIGTERM` to stop cleanly.

---

## Running inside Kubernetes

1. **Build and push** the image:

   ```bash
   docker build -t tars:latest .
   # docker tag tars:latest <registry>/tars:latest
   # docker push <registry>/tars:latest
   ```

2. **Create the namespace** (the manifests use `tars`):

   ```bash
   kubectl create namespace tars
   ```

3. **Apply the manifests**, then edit the Deployment's `SERVICE_NAME` env var
   to point at the Service you want to watch:

   ```bash
   kubectl apply -f manifests/
   kubectl -n tars set env deployment/tars SERVICE_NAME=nginx
   ```

4. **Tail the logs:**

   ```bash
   kubectl -n tars logs -f deployment/tars
   ```

---

## Running with Helm

The Helm chart lives in `chart/`. Build and push your image first:

```bash
docker build -t <registry>/tars:latest .
docker push <registry>/tars:latest
```

Install TARS into the namespace that contains the Service you want to monitor:

```bash
helm upgrade --install tars ./chart \
  --namespace ingress-nginx \
  --create-namespace \
  --set image.repository=<registry>/tars \
  --set image.tag=latest \
  --set serviceName=nginx \
  --set checkInterval=300
```

Tail logs:

```bash
kubectl -n ingress-nginx logs -f deployment/tars
```

Useful chart values:

| Value | Default | Description |
|-------|---------|-------------|
| `image.repository` | `tars` | Container image repository. |
| `image.tag` | `latest` | Container image tag. |
| `image.pullPolicy` | `IfNotPresent` | Image pull policy. |
| `serviceName` | `nginx` | Service to monitor in the Helm release namespace. |
| `checkInterval` | `300` | Seconds between checks. |
| `resources` | small defaults | CPU/memory requests and limits. |
| `serviceAccount.create` | `true` | Whether the chart creates a ServiceAccount. |

---

## Required RBAC permissions

The monitor needs **read-only** access to Services and EndpointSlices in its
own namespace only, granted by the included `Role`:

| API group            | Resource         | Verbs        |
|----------------------|------------------|--------------|
| `""` (core)          | `services`       | `get`        |
| `discovery.k8s.io`   | `endpointslices` | `list`       |

The Deployment sets `NAMESPACE` from the pod namespace, so deploy one copy in
the namespace that contains the Service you want to monitor.

---

## Example logs

TARS emits INFO-level JSON logs. Startup and shutdown records are simple
operational messages. Each check emits either a successful check record or a
failed check record.

### INFO — successful check (TAR active)

```json
{
  "timestamp": "2026-07-13T10:15:30Z",
  "level": "INFO",
  "namespace": "ingress-nginx",
  "service": "nginx",
  "tar_enabled": true,
  "annotation_present": true,
  "endpoint_slices": 2,
  "total_endpoints": 6,
  "ready_endpoints": 6,
  "hinted_endpoints": 6,
  "check_duration_ms": 31,
  "status": "ENABLED",
  "disabled_reason": null,
  "message": "Topology Aware Routing check completed"
}
```

### INFO — successful check (TAR inactive)

```json
{
  "timestamp": "2026-07-13T10:20:30Z",
  "level": "INFO",
  "namespace": "ingress-nginx",
  "service": "nginx",
  "tar_enabled": false,
  "annotation_present": true,
  "endpoint_slices": 2,
  "total_endpoints": 6,
  "ready_endpoints": 6,
  "hinted_endpoints": 0,
  "check_duration_ms": 27,
  "status": "DISABLED",
  "disabled_reason": "not all ready endpoints have topology hints",
  "message": "Topology Aware Routing check completed"
}
```

### INFO — check failed (e.g. RBAC denied, Service not found)

```json
{
  "timestamp": "2026-07-13T10:25:30Z",
  "level": "INFO",
  "namespace": "ingress-nginx",
  "service": "nginx",
  "error": "(403)",
  "check_duration_ms": 12,
  "message": "Topology Aware Routing check failed"
}
```

On error the monitor logs the failure and keeps running; the next interval
retries automatically.

### Log field reference

| Field | Meaning |
|-------|---------|
| `timestamp` | UTC timestamp when the log was emitted. |
| `level` | Always `INFO`. |
| `namespace` | Namespace being monitored. |
| `service` | Service name being monitored. |
| `tar_enabled` | Boolean verdict for TAR status. Present on successful check logs. |
| `annotation_present` | Whether the Service has `service.kubernetes.io/topology-mode`. |
| `endpoint_slices` | Number of matching EndpointSlices. |
| `total_endpoints` | Total endpoints across matching EndpointSlices. |
| `ready_endpoints` | Endpoints considered ready. Missing `conditions.ready` counts as ready, matching the EndpointSlice API default. |
| `hinted_endpoints` | Endpoints that have `hints.forZones`, ready or not. |
| `check_duration_ms` | How long the check took in milliseconds. |
| `status` | `ENABLED` or `DISABLED`. |
| `disabled_reason` | `null` when enabled; otherwise explains why TAR is disabled. |
| `error` | Error text for failed checks, such as RBAC denial or missing Service. |
| `message` | Human-readable event message. |

---

## Notes on OKD 4.13 / Kubernetes 1.26

* EndpointSlices are `discovery.k8s.io/v1` (GA since 1.21).
* The topology-aware feature gate (`TopologyAwareHints`) is enabled by default
  in 1.26; opt-in is via the `service.kubernetes.io/topology-mode: Auto`
  annotation on the Service.
* The controller places hints based on node `topology.kubernetes.io/zone`
  labels, so nodes must be zone-labeled for hints to appear.
