import re
import subprocess
import socket

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

    # ensure starts with letter
    if not re.match(r'^[a-z]', name):
        name = f"a{name}"
    return name


def normalize_dataset_image_name(gs_url: str) -> str:
    """
    Normalize a GCS URL (gs://bucket/path/file) into a valid GCP snapshot name.
    Examples:
      gs://testexperiment/input/       => dataset--testexperiment--input
      gs://testexperiment/input/file.gz => dataset--testexperiment--input-file
    """
    # Remove gs:// prefix and split into segments
    if gs_url.startswith("gs://"):
        path = gs_url[len("gs://"):]
    else:
        path = gs_url
    segments = [seg for seg in re.split(r'[/]+', path) if seg]

    # Remove file extension for last segment if present
    if segments and '.' in segments[-1]:
        segments[-1] = segments[-1].rsplit('.', 1)[0]

    # Join segments with '--'
    base = '--'.join(segments)

    # Only allow a-z, 0-9, and dash
    name = re.sub(r'[^a-zA-Z0-9-]', '-', base).lower()

    # Collapse multiple dashes
    name = re.sub(r'-{2,}', '-', name)

    # Ensure starts with a letter
    if not name or not name[0].isalpha():
        name = 'a' + name

    # Strip trailing dashes
    name = name.rstrip('-')

    # Truncate to 63 chars max, strip trailing dash again if necessary
    name = name[:63].rstrip('-')

    return f"gs--{name}"


def normalize_docker_image_name(docker_url: str, prefix="docker") -> str:
    """
    Converts a docker URL (with optional tag) into a valid Google image name.

    Steps:
      1. Extract the image name (after the last '/') and strip off any tag.
      2. Lowercase everything.
      3. Replace invalid chars with '-'.
      4. Collapse multiple '-' runs.
      5. Ensure it starts with a letter (prepend 'a' if not).
      6. Strip trailing '-'s.
      7. Truncate to 63 chars and strip trailing '-' again.
    """
    # 1. extract last segment and drop tag
    image_part = docker_url.rsplit('/', 1)[-1]
    base_name  = image_part.split(':', 1)[0].lower()

    # 2–3. replace any non a-z/0-9/- with '-'
    name = re.sub(r'[^a-z0-9-]', '-', base_name)

    # 4. collapse duplicate hyphens
    name = re.sub(r'-{2,}', '-', name)

    # 5. ensure start with letter
    if not name or not name[0].isalpha():
        name = 'a' + name

    # 6. strip trailing hyphens
    name = name.rstrip('-')

    # 7. enforce max length of 63
    name = name[:30].rstrip('-')

    return f"{prefix}-{name}"


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
        raise RuntimeError(f"Unexpected error checking image {image}: {e}")


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

def normalize_name(name: str) -> str:
    """
    Normalize string for GCP snapshot naming:
    - lowercase
    - replace invalid chars with '-'
    - must start with a letter
    - must end with a letter or digit
    - max length 63 chars
    """
    # Lowercase and replace invalid chars
    name = name.lower()
    name = re.sub(r'[^a-z0-9-]', '-', name)

    # Ensure starts with a letter
    if not re.match(r'^[a-z]', name):
        name = 'a' + name

    # Ensure ends with letter or digit
    if not re.search(r'[a-z0-9]$', name):
        name = name.rstrip('-')  # remove trailing dash
        if not re.search(r'[a-z0-9]$', name):
            name += 'a'

    # Enforce max length 63
    name = name[:63]

    # If truncation causes trailing dash, fix it
    if name.endswith('-'):
        name = name.rstrip('-') + 'a'

    return name


# def get_hf_or_local_image(model_repo: str, branch: str = ""):
#     is_snapshot = False
#
#     if "/" in model_repo:
#         user, model = model_repo.split("/", 1)
#         user = normalize_name(user)
#
#         if branch:
#             model = f"{model}-{branch}"
#
#         model = normalize_name(model)
#         snapshot = f"{user}--{model}"
#
#     else:
#         snapshot = normalize_name(model_repo)
#         is_snapshot = True
#
#     return snapshot, is_snapshot

def is_port_open(host, port, timeout=3):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False