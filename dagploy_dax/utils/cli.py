import re
import subprocess
import json

def normalize_image_name(base_name: str) -> str:
    """
    Normalize base_name to meet GCP image naming rules:
    - lowercase letters, numbers, hyphens
    - must start with a lowercase letter
    """
    # lowercase
    name = base_name.lower()
    # replace invalid chars with hyphens
    name = re.sub(r'[^a-z0-9-]', '-', name)
    # collapse multiple hyphens
    name = re.sub(r'-{2,}', '-', name)
    # strip leading/trailing hyphens
    name = name.strip('-')

    # limit maximum characters
    name = name[:55]

    # ensure starts with letter
    if not re.match(r'^[a-z]', name):
        name = f"a{name}"

    # must end with letter or digit
    if not re.match(r'.*[a-z0-9]$', name):
        name = name + '0'

    return name


def snapshot_exists(snapshot_name: str, project: str) -> bool:
    """
    Return True if `gcloud compute snapshots describe` succeeds.
    """
    try:
        subprocess.run(
            [
                "gcloud", "compute", "snapshots", "describe", snapshot_name,
                "--project", project, "--format", "none"
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except subprocess.CalledProcessError:
        return False


def image_exists(image: str, project: str):
    """
    Checks if a GCP image exists. Supports image in current or another project.
    Returns image info dict if exists, else None.
    """
    cmd = [
        "gcloud", "compute", "images", "describe", image,
        "--project", project,
        "--format", "json"
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, check=True, text=True)
        image_info = json.loads(result.stdout)
        return image_info
    except subprocess.CalledProcessError:
        return None
    except Exception as e:
        raise RuntimeError(f"Unexpected error checking image {image} with {cmd} : {e}")
