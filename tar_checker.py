"""Topology Aware Routing (Topology Aware Hints) verification logic.

A Service is reported as having Topology Aware Routing ENABLED only when all
three conditions hold:

1. at least one EndpointSlice exists for the Service,
2. at least one of its endpoints is ready, and
3. **every** ready endpoint carries topology hints (``hints.forZones``).

The hint-placement algorithm in the EndpointSlice controller is all-or-nothing
per slice, so requiring every ready endpoint to be hinted reflects whether TAR
is genuinely active rather than only partially applied.
"""

from __future__ import annotations

from dataclasses import dataclass

from kubernetes.client import (
    CoreV1Api,
    DiscoveryV1Api,
    V1Endpoint,
    V1EndpointSlice,
    V1Service,
)

# Annotation that opts a Service into Topology Aware Routing.
# Value ``Auto`` lets the controller place hints; ``Disabled`` opts out.
# Ref: https://kubernetes.io/docs/concepts/services-networking/topology-aware-routing/
TOPOLOGY_MODE_ANNOTATION = "service.kubernetes.io/topology-mode"

# Pre-1.27 annotation name (alias) that opts a Service into Topology Aware
# Routing. It was renamed to ``topology-mode`` in Kubernetes 1.27, but clusters
# older than 1.27 still write this key. TAR evaluation accepts either.
# Ref: https://kubernetes.io/docs/concepts/services-networking/topology-aware-routing/
TOPOLOGY_AWARE_HINTS_ANNOTATION = "service.kubernetes.io/topology-aware-hints"

# Standard label that links an EndpointSlice to its parent Service.
SERVICE_NAME_LABEL = "kubernetes.io/service-name"

STATUS_ENABLED = "ENABLED"
STATUS_DISABLED = "DISABLED"


@dataclass(frozen=True)
class CheckResult:
    """Outcome of a single TAR verification pass."""

    annotation_present: bool
    topology_mode_present: bool
    topology_aware_hints_present: bool
    endpoint_slices: int
    total_endpoints: int
    ready_endpoints: int
    hinted_endpoints: int
    tar_enabled: bool
    status: str
    disabled_reason: str | None


def get_service(core_v1: CoreV1Api, namespace: str, name: str) -> V1Service:
    """Read a single Service from the cluster."""
    return core_v1.read_namespaced_service(name=name, namespace=namespace)


def _service_annotations(service: V1Service) -> dict[str, str]:
    """Return the Service annotations as a dict (empty when unset)."""
    if service.metadata is None:
        return {}
    return service.metadata.annotations or {}


def is_tar_annotation_present(service: V1Service) -> bool:
    """Return True when a topology-routing annotation is set on the Service.

    Accepts both the current ``topology-mode`` annotation (1.27+) and the
    older ``topology-aware-hints`` annotation used by Kubernetes < 1.27.
    """
    annotations = _service_annotations(service)
    return (
        TOPOLOGY_MODE_ANNOTATION in annotations
        or TOPOLOGY_AWARE_HINTS_ANNOTATION in annotations
    )


def list_endpoint_slices(
    discovery_v1: DiscoveryV1Api, namespace: str, service_name: str
) -> list[V1EndpointSlice]:
    """Return every EndpointSlice owned by ``service_name`` in ``namespace``."""
    response = discovery_v1.list_namespaced_endpoint_slice(
        namespace=namespace,
        label_selector=f"{SERVICE_NAME_LABEL}={service_name}",
    )
    return list(response.items or [])


def is_endpoint_ready(endpoint: V1Endpoint) -> bool:
    """Whether an endpoint is considered ready.

    Per the EndpointSlice API, a missing ``conditions.ready`` is equivalent to
    ready (defaults to true); only an explicit ``False`` counts as not-ready.
    """
    conditions = endpoint.conditions
    if conditions is None or conditions.ready is None:
        return True
    return bool(conditions.ready)


def endpoint_has_hints(endpoint: V1Endpoint) -> bool:
    """Whether an endpoint carries topology hints (``hints.forZones``)."""
    hints = endpoint.hints
    return bool(hints and hints.for_zones)


def analyze_endpoint_slices(
    slices: list[V1EndpointSlice],
) -> tuple[int, int, int, int, int]:
    """Reduce EndpointSlices into aggregate counts.

    Returns ``(slice_count, total, ready, hinted, ready_with_hints)``.
    Only ``ready_with_hints`` drives the final ENABLED/DISABLED verdict.
    """
    total = ready = hinted = ready_with_hints = 0

    for ep_slice in slices:
        for endpoint in ep_slice.endpoints or []:
            total += 1
            endpoint_ready = is_endpoint_ready(endpoint)
            has_hints = endpoint_has_hints(endpoint)

            if endpoint_ready:
                ready += 1
                if has_hints:
                    ready_with_hints += 1
            if has_hints:
                hinted += 1

    return len(slices), total, ready, hinted, ready_with_hints


def disabled_reason(slice_count: int, ready: int, ready_with_hints: int) -> str | None:
    """Apply the ENABLED ruleset (see module docstring)."""
    if slice_count == 0:
        return "no EndpointSlices found for Service"
    if ready == 0:
        return "no ready endpoints found"
    if ready_with_hints != ready:
        return "not all ready endpoints have topology hints"
    return None


def check_tar(
    core_v1: CoreV1Api,
    discovery_v1: DiscoveryV1Api,
    namespace: str,
    service_name: str,
) -> CheckResult:
    """Perform one full TAR verification pass for the given Service."""
    service = get_service(core_v1, namespace, service_name)
    annotations = _service_annotations(service)
    topology_mode_present = TOPOLOGY_MODE_ANNOTATION in annotations
    topology_aware_hints_present = TOPOLOGY_AWARE_HINTS_ANNOTATION in annotations
    annotation_present = topology_mode_present or topology_aware_hints_present

    slices = list_endpoint_slices(discovery_v1, namespace, service_name)
    slice_count, total, ready, hinted, ready_with_hints = analyze_endpoint_slices(slices)

    reason = disabled_reason(slice_count, ready, ready_with_hints)
    tar_enabled = reason is None
    return CheckResult(
        annotation_present=annotation_present,
        topology_mode_present=topology_mode_present,
        topology_aware_hints_present=topology_aware_hints_present,
        endpoint_slices=slice_count,
        total_endpoints=total,
        ready_endpoints=ready,
        hinted_endpoints=hinted,
        tar_enabled=tar_enabled,
        status=STATUS_ENABLED if tar_enabled else STATUS_DISABLED,
        disabled_reason=reason,
    )
