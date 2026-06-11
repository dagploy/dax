from google.cloud import secretmanager
from google.api_core.exceptions import NotFound, PermissionDenied

def read_gcp_secret(
    *,
    project_id: str,
    secret_name: str,
    version: str = "latest",
) -> str:
    """
    Read a secret value from GCP Secret Manager.
    """
    if not project_id:
        raise ValueError("project_id is required")

    if not secret_name:
        raise ValueError("secret_name is required")

    client = secretmanager.SecretManagerServiceClient()

    resource_name = (
        f"projects/{project_id}/secrets/{secret_name}/versions/{version}"
    )

    try:
        response = client.access_secret_version(name=resource_name)
    except NotFound as exc:
        raise RuntimeError(
            f"GCP Secret Manager secret not found: "
            f"project={project_id}, secret={secret_name}, version={version}"
        ) from exc
    except PermissionDenied as exc:
        raise RuntimeError(
            f"Permission denied accessing GCP Secret Manager secret: "
            f"project={project_id}, secret={secret_name}, version={version}. "
            "Check roles/secretmanager.secretAccessor for the Pulumi runner identity."
        ) from exc

    value = response.payload.data.decode("utf-8")

    if not value:
        raise RuntimeError(
            f"GCP Secret Manager secret is empty: "
            f"project={project_id}, secret={secret_name}, version={version}"
        )

    return value

