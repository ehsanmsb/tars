"""Kubernetes API client initialization.

The client is configured automatically:

* inside a Pod the in-cluster service-account credentials are used, and
* outside the cluster the local kubeconfig (``~/.kube/config``) is used.
"""

from __future__ import annotations

from kubernetes import config
from kubernetes.client import ApiClient, CoreV1Api, DiscoveryV1Api
from kubernetes.config import ConfigException


class KubernetesConfigError(RuntimeError):
    """Raised when no usable Kubernetes configuration can be found."""


def load_api_clients() -> tuple[CoreV1Api, DiscoveryV1Api]:
    """Return ``(CoreV1Api, DiscoveryV1Api)`` sharing one underlying client.

    Tries in-cluster configuration first (the production path); falls back to
    the local kubeconfig for local development. Raises
    :class:`KubernetesConfigError` if neither is available.
    """
    try:
        config.load_incluster_config()
    except ConfigException:
        try:
            config.load_kube_config()
        except ConfigException as exc:
            raise KubernetesConfigError(
                "Unable to load Kubernetes configuration: not running in-cluster "
                "and no valid kubeconfig was found"
            ) from exc

    api_client = ApiClient()
    return CoreV1Api(api_client), DiscoveryV1Api(api_client)
