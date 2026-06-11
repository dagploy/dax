import yaml
from io import StringIO

import time
import textwrap
import re
import json
import ast
import subprocess
import toml
from pulumi_command import local

import pulumi
from pulumi import ResourceOptions, InvokeOptions

from itertools import count
from pulumi_gcp import compute
from pathlib import Path

from typing import Dict, Any, Sequence
from string import Template

from pulumi_command import local as command

def sanitize_disk_name(raw: str) -> str:
    name = raw.lower()
    name = re.sub(r"[^a-z0-9-]", "-", name)
    name = name[:61].strip("-")
    return name or "disk"

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


def instance_exists(name: str, zone: str = None, project: str = None) -> bool:
    """Check if any GCE instance with the given name exists (any status)."""
    cmd = [
        "gcloud", "compute", "instances", "list",
        f"--filter=name={name}",
        "--format=value(name)"
    ]

    if project:
        cmd.insert(2, f"--project={project}")
    try:
        out = subprocess.check_output(cmd).decode().strip()
        return bool(out)
    except subprocess.CalledProcessError:
        return False

def choose_vm_name(project: str, zone: str, vm_name: str) -> str:
    """
    Pick a VM name from Pulumi.dev.yaml config `gcp_hf:vm_name`,
    defaulting to 'notebook-hf-spot'. If that name is already
    in use (any status), try with incremental numeric suffixes
    (e.g. base, base-2, base-3, …) until an unused name is found.
    """
    for idx in count(1):
        candidate = vm_name if idx == 1 else f"{vm_name}-{idx}"
        if not instance_exists(candidate, zone, project):
            return candidate

    # Should never get here
    raise RuntimeError("Unable to generate a unique VM name")


def snapshot_exists(snapshot_name: str, project: str) -> bool:
    """Return True if `gcloud compute snapshots describe` succeeds."""
    cmd = [
        "gcloud", "compute", "snapshots", "describe", snapshot_name,
        "--project", project,
        "--format", "json"
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, check=True, text=True)
        snap_info = json.loads(result.stdout)
        return snap_info
    except subprocess.CalledProcessError as e:
        return False
    except Exception as e:
        raise RuntimeError(f"Unexpected error getting snapshot info: {e}")


def set_default_disk_instance(cfg):
    machine_type = str(cfg.get("machine_type", "")).lower()
    if not machine_type:
        raise ValueError("Missing 'machine_type' in cfg")

    # Detect machine family (e.g. "n4", "c4", "e2")
    machine_family = machine_type.split("-")[0]

    # Determine correct disk type
    if machine_family in {"n4", "c4", "g4"}:
        disk_type_default = "hyperdisk-balanced"
    else:
        disk_type_default = "pd-ssd"

    return disk_type_default


def prepare_disk_from_images(cfg):
    disk_executions = []
    disk_device_names = []

    # adjust disk based on instance type
    disk_type_default = set_default_disk_instance(cfg)

    if "images" in cfg and isinstance(cfg["images"], list) and len(cfg["images"]) > 0:
        for idx, image_name in enumerate(cfg['images']):
            # lookup and validate the image
            img = image_exists(image_name, cfg["project"])
            if not img:
                raise ValueError(f"❌ Disk image '{image_name}' is not found.")

            # build a unique disk name
            disk_name = f"{cfg['stack_name']}-disk-{idx}"

            # decide on disk size
            #  you can override via cfg['disk_size_override_gb'], else use the image's size
            size_gb = int(img["diskSizeGb"])

            # Assemble the args for compute.Disk
            disk_args: Dict[str, Any] = {
                "name": disk_name,
                "zone": cfg["zone"],
                "type": disk_type_default,
                "size": size_gb,
                "image": img['selfLink'],
            }

            disk_executions.append({"disk_name": disk_name, "disk_args": disk_args})
            disk_device_names.append(disk_name)

    return disk_executions, disk_device_names


def create_image_disks(cfg):
    cache_disks = []
    disk_device_names = []

    disk_type_default = set_default_disk_instance(cfg)

    for idx, _img in enumerate(cfg.get("images", [])):
        disk_name = f"{cfg['stack_name']}-{idx}"

        # Base arguments
        disk_args = {
            "name": disk_name,
            "zone": cfg["zone"],
            "type": disk_type_default
        }

        # Look up image metadata if it exists
        img = image_exists(_img, cfg["project"])

        # Decide final disk size (image size or configured override)
        if img:
            base_size = int(img["diskSizeGb"])
            user_size = int(cfg.get("image_size", 0)) or base_size
            # Use the larger of the two — GCP requires disk >= image
            final_size = max(base_size, user_size)
            disk_args["image"] = img["selfLink"]
            disk_args["size"] = final_size
        else:
            # No image found — fall back to user-defined size (required)
            if not cfg.get("image_size"):
                raise ValueError(
                    f"Image '{_img}' not found and no 'image_size' provided."
                )
            disk_args["size"] = int(cfg["image_size"])

        # Create disk resource
        disk = compute.Disk(
            f"cacheDisk-{idx}",
            **disk_args,
        )

        cache_disks.append(disk)
        disk_device_names.append(disk_name)

    return cache_disks, disk_device_names


def resolve_image_and_size(cfg: dict, boot_source_image: str) -> tuple[str, int]:
    """
    Resolve image reference and image restore size.

    Supports:
    - short image name: demo-g4-golden-image
    - project image path: projects/my-project/global/images/demo-g4-golden-image
    """

    project = cfg["project"]

    if boot_source_image.startswith("projects/"):
        # projects/<project>/global/images/<image>
        parts = boot_source_image.split("/")
        if len(parts) < 5:
            raise ValueError(f"Invalid boot_source_image path: {boot_source_image}")

        image_project = parts[1]
        image_name = parts[-1]

        image = compute.get_image(
            name=image_name,
            project=image_project,
        )

        return image.self_link, int(image.disk_size_gb)

    image = compute.get_image(
        name=boot_source_image,
        project=project,
    )

    return image.self_link, int(image.disk_size_gb)


def provision_gcp_vm(cfg: dict, depends_on=None, attached_disks_args=None) -> compute.Instance:
    """
    Provisions a GCP compute instance with an attached persistent disk using a single config dict.
    """
    if depends_on is None:
        depends_on = []

    if attached_disks_args is None:
        attached_disks_args = []

    open_public_access = cfg.get("public_mode", False)

    # Private-only VM → no access_configs
    network_interfaces = [
        compute.InstanceNetworkInterfaceArgs(
            subnetwork=cfg["subnetwork"],
            access_configs=(
                [compute.InstanceNetworkInterfaceAccessConfigArgs()]
                if open_public_access else []
            ),
        )
    ]

    machine_type = str(cfg.get("machine_type", "")).lower()
    provisioning = str(cfg.get("provisioning_model", "standard")).strip().upper()

    # Parse gpu_count robustly (handles None/"", "1", 1)
    gpu_count_raw = cfg.get("gpu_count", 0)

    try:
        gpu_count = int(gpu_count_raw) if gpu_count_raw not in (None, "") else 0
    except (TypeError, ValueError):
        gpu_count = 0

    # Families that imply GPU even without explicit count (e.g., G2/L4, A2/A3)
    machine_family = machine_type.split("-")[0] if "-" in machine_type else ""
    gpu_families = {"g2", "a2", "a3", "g4"}

    has_gpu = (gpu_count > 0) or (machine_family in gpu_families)

    if provisioning == "SPOT":
        # Spot/preemptible must always TERMINATE
        scheduling_args = compute.InstanceSchedulingArgs(
            provisioning_model="SPOT",
            preemptible=True,  # 👈 required
            automatic_restart=False,
            on_host_maintenance="TERMINATE",
        )
    else:
        # Standard (non-preemptible)
        if has_gpu:
            on_host_maintenance = "TERMINATE"
        elif machine_family.startswith("e2"):
            on_host_maintenance = "MIGRATE"
        else:
            on_host_maintenance = "MIGRATE"

        scheduling_args = compute.InstanceSchedulingArgs(
            provisioning_model="STANDARD",
            preemptible=False,  # 👈 explicitly safe
            automatic_restart=True,
            on_host_maintenance=on_host_maintenance,
        )

    # Safety net: forbid GPU + MIGRATE
    if has_gpu and scheduling_args.on_host_maintenance != "TERMINATE":
        raise ValueError("GPU instances must use on_host_maintenance='TERMINATE'.")

    boot_size = cfg.get("boot_size", 150)
    disk_type_default = set_default_disk_instance(cfg)

    # handle the boot on disk image
    boot_source_image = cfg.get("boot_source_image")    # image to create disk from (name or selfLink)
    existing_boot_disk_name = cfg.get("existing_boot_disk_name")  # optional: attach existing disk directly

    # Only needed when creating a new disk from boot_source_image.
    # If not provided, derive a stable disk name from the VM name.
    boot_disk_name = cfg.get("boot_disk_name") or f"{cfg['vm_name']}-boot"

    # --- Boot disk resolution ---
    boot_disk_resource = None

    # --- Handle load_startup_script flag ---
    if "load_startup_script" in cfg:
        load_startup = convert_to_boolean(cfg["load_startup_script"])
        if not load_startup:
            cfg["startup_script"] = "STARTUP_SCRIPT_COMPLETE"

    if existing_boot_disk_name:
        # --------------------------------------------------
        # BOOT FROM EXISTING DISK BY NAME (disk must already exist)
        # --------------------------------------------------
        boot_disk = compute.InstanceBootDiskArgs(
            source=f"projects/{cfg['project']}/zones/{cfg['zone']}/disks/{existing_boot_disk_name}",
            auto_delete=False,
        )

    elif boot_source_image:
        # --------------------------------------------------
        # BOOT FROM IMAGE, BUT CREATE A *NAMED DISK* FIRST
        # --------------------------------------------------
        image_ref, image_boot_size = resolve_image_and_size(cfg, boot_source_image)

        boot_disk = compute.InstanceBootDiskArgs(
            initialize_params=compute.InstanceBootDiskInitializeParamsArgs(
                image=image_ref,
                size=image_boot_size,
                type=disk_type_default,
            ),
            auto_delete=True,
        )

    else:
        # --------------------------------------------------
        # DEFAULT: BOOT DISK INITIALIZED DIRECTLY FROM IMAGE (unnamed disk)
        # --------------------------------------------------
        boot_disk = compute.InstanceBootDiskArgs(
            initialize_params=compute.InstanceBootDiskInitializeParamsArgs(
                image=cfg["os_image"],
                size=boot_size,
                type=disk_type_default,
            ),
            auto_delete=True,
        )

    # assign nat proxy
    if not open_public_access:
        cfg['tags'] = cfg['tags'] + ['nat']

    compute_instance = compute.Instance(
        "computeInstance",
        labels={"deploy-target": "true"},
        name=cfg["vm_name"],
        zone=cfg["zone"],
        machine_type=cfg["machine_type"],
        tags=cfg['tags'],
        boot_disk=boot_disk,
        allow_stopping_for_update=True,
        network_interfaces=network_interfaces,
        attached_disks=attached_disks_args,
        service_account=compute.InstanceServiceAccountArgs(
            email=cfg["service_account"],
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        ),
        scheduling=scheduling_args,
        metadata={
            "enable-oslogin": cfg.get('os_login', "TRUE").upper(),
            "serial-port-enable": "TRUE",
        },
        metadata_startup_script=cfg["startup_script"],
        opts=ResourceOptions(
            depends_on=[
                *depends_on,
                *( [boot_disk_resource] if boot_disk_resource else [] )
            ]
        ),
    )

    return compute_instance

def convert_to_boolean(val, default=False):
    if isinstance(val, bool):
        return val
    if val is None:
        return default
    if isinstance(val, (int, float)):
        return val != 0
    if isinstance(val, str):
        v = val.strip().lower()
        if v in {"true","1","yes","y","on"}:
            return True
        if v in {"false","0","no","n","off",""}:
            return False
    raise ValueError(f"Cannot interpret {val!r} as boolean")


def convert_strings_to_list(list_string):
    # Defensive: if list_string is a string, try to parse as list or single entry
    if isinstance(list_string, str):
        try:
            # Try to parse as Python literal list
            val = ast.literal_eval(list_string)
            if isinstance(val, list):
                list_string = val
            else:
                list_string = [val]
        except Exception:
            # Could be a comma/space separated list, fallback
            list_string = [s.strip() for s in list_string.replace(",", " ").split()]

    parsed = []
    for entry in list_string or []:
        parsed.append(entry)

    if parsed:
        # ensure unique list, no duplication
        parsed = list(set(parsed))

    return parsed

def sanitize_gcp_tag(tag: str) -> str:
    # Replace underscores with dashes, remove invalid chars, enforce length
    tag = tag.lower().replace("_", "-")
    tag = re.sub(r"[^a-z0-9-]", "", tag)
    return tag[:61]  # GCP tags max length


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


# Helper: zone -> region (e.g., asia-southeast1-b -> asia-southeast1)
def zone_to_region(z: str) -> str:
    parts = z.rsplit("-", 1)
    return parts[0] if len(parts) == 2 else z

def gpu_validation_resource(cfg):
    machine_type = cfg["machine_type"]
    zone = cfg["zone"]
    return command.Command(
        "gpu-validation",
        create=f"python3 -c 'import requests; "
               f"import sys; "
               f"r=requests.get(\"https://compute.googleapis.com/compute/v1/projects/{cfg['project']}/zones/{zone}/machineTypes/{machine_type}\"); "
               f"sys.exit(0 if r.status_code==200 else 1)'"
    )


def load_gpu_mapping(path: str) -> dict:
    """
    Load the machine_type → gpu_count mapping from a TOML file.
    """
    data = toml.load(path)
    # keys are machine_type strings, values integers (gpu count)
    return data

def _determine_gpu_count(machine_type: str, gpu_map: dict, default: int = 0) -> int:
    """
    Determine number of GPUs for a given machine_type.
    If mapping value is -1: treat as “unspecified/attachable” and return default.
    """
    if machine_type in gpu_map:
        gpu_count = gpu_map.get(machine_type, -1)
        if gpu_count < 0:
            # unspecified: user must define or we fallback to default
            return default
        return gpu_count
    # fallback: no mapping found
    return default

def set_gpu_from_machine_type(cfg: dict, mapping_path: str = "gpu_mapping.toml", default_gpu: int = 0) -> dict:
    """
    Given cfg dict with key "machine_type", sets cfg["gpu"] based on mapping.
    Loads mapping from the given TOML file.
    """
    machine_type = cfg.get("machine_type")
    if not machine_type:
        raise ValueError("cfg must include 'machine_type' key")

    gpu_map = load_gpu_mapping(mapping_path)
    gpu_count = _determine_gpu_count(machine_type, gpu_map, default_gpu)

    # Update GPU config based on available GPU count
    if gpu_count > 0:
        cfg["gpu"] = gpu_count
    else:
        cfg.pop("gpu", None)

    pulumi.log.info(f"Mapping GPU : {machine_type} with gpu count: {gpu_count}")

    return cfg

def prepare_cfg(cfg):
    # ✅ Add computed helpers
    if "zone" in cfg and "-" in cfg["zone"]:
        cfg["region"] = cfg["zone"].rsplit("-", 1)[0]

    if "images" in cfg:
        cfg["image_names"] = " ".join(cfg["images"])

    if "disk_device_names" in cfg:
        cfg["disk_device_names"] = " ".join(cfg["disk_device_names"])

    # Default mode → service
    if "service" in cfg:
        cfg["mode"] = cfg["service"]

    return cfg


def to_bash_vars(cfg: dict) -> str:
    """Convert a config dict into bash variable declarations."""
    lines = []

    # ✅ computed helper: region from zone
    if "zone" in cfg and "-" in cfg["zone"]:
        cfg["region"] = cfg["zone"].rsplit("-", 1)[0]

    for k, v in cfg.items():
        name = k.upper()

        if v is None:
            continue
        elif isinstance(v, list):
            # bash array syntax
            joined = " ".join(map(str, v))
            lines.append(f'{name}=({joined})')
        else:
            safe_val = str(v).replace('"', '\\"')
            lines.append(f'{name}="{safe_val}"')

    return "\n".join(lines)


def normalize_ports(value: object) -> list[str]:
    if value is None:
        ports = []
    elif isinstance(value, str):
        ports = [p.strip() for p in value.split(",") if p.strip()]
    else:
        ports = list(value)

    return sorted({str(p) for p in [*ports, 80]})


def setup_firewall(cfg: dict[str, Any], depends_on: Sequence[Any] | None = None) -> None:
    depends_on = list(depends_on or [])

    stack = cfg["stack_name"]
    network = cfg["network"]

    tags = list(cfg.get("tags") or [])
    if not tags:
        raise ValueError("cfg['tags'] is required for firewall target_tags.")

    firewall_ports = normalize_ports(cfg.get("open_ports"))

    public_mode = cfg.get("public_mode") is True
    iap_login = cfg.get("iap_login") is True
    internal_domain = bool(cfg.get("internal_domain"))

    firewall_rules: list[pulumi.Output[str]] = []

    def create_firewall(
        resource_name: str,
        name: str,
        source_ranges: list[str],
        description: str,
    ) -> None:
        fw = compute.Firewall(
            resource_name,
            name=name,
            network=network,
            direction="INGRESS",
            priority=1000,
            allows=[
                compute.FirewallAllowArgs(
                    protocol="tcp",
                    ports=firewall_ports,
                )
            ],
            source_ranges=source_ranges,
            target_tags=tags,
            description=description,
            opts=ResourceOptions(depends_on=depends_on),
        )

        firewall_rules.append(fw.name)

    if public_mode:
        create_firewall(
            resource_name=f"{stack}-public-fw",
            name=f"allow-{stack}-public",
            source_ranges=["0.0.0.0/0"],
            description=f"Allow public TCP access for {stack}",
        )

    if iap_login:
        create_firewall(
            resource_name=f"{stack}-iap-fw",
            name=f"allow-{stack}-iap",
            source_ranges=[
                "35.191.0.0/16",
                "130.211.0.0/22",
                "35.235.240.0/20",
            ],
            description=f"Allow IAP/LB access for {stack}",
        )

    if internal_domain:
        create_firewall(
            resource_name=f"{stack}-internal-fw",
            name=f"allow-{stack}-internal",
            source_ranges=cfg.get("internal_source_ranges")
            or [
                "10.0.0.0/8",
                "172.16.0.0/12",
                "192.168.0.0/16",
            ],
            description=f"Allow internal VPC access for {stack}",
        )

    pulumi.export("firewall_rules", firewall_rules)
    pulumi.export("firewall_ports", firewall_ports)
    pulumi.export("firewall_tags", tags)


def camel_to_snake(name: str) -> str:
    """Convert camelCase or PascalCase to snake_case (lowercase with underscores)."""
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)  # handle aB → a_B
    name = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)  # handle ABCd → AB_Cd
    return name.lower()


def load_pulumi_config(config) -> dict:
    """
    Load full Pulumi stack configuration as a dict.
    - Removes project prefix (e.g., 'project:key' -> 'key')
    - Converts camelCase/PascalCase to snake_case
    - Converts numeric strings to int/float if possible
    """
    stack_name = pulumi.get_stack()
    infra_yaml_path = Path(config.require("infra_yaml_path")).resolve()

    if not infra_yaml_path.exists():
        raise FileNotFoundError(f"Infra path not found: {infra_yaml_path}")

    result = subprocess.run(
        ["pulumi", "config", "--stack", stack_name, "--json"],
        cwd=str(infra_yaml_path),
        capture_output=True,
        text=True,
        check=True,
    )

    raw_data = json.loads(result.stdout)
    cleaned = {}

    for full_key, obj in raw_data.items():
        # Remove stack/project prefix (e.g., "myproj:key" → "key")
        key = full_key.split(":", 1)[-1] if ":" in full_key else full_key
        key = camel_to_snake(key)

        val = obj.get("value")

        # Try to coerce numeric strings
        if isinstance(val, str):
            if val.isdigit():
                val = int(val)
            else:
                try:
                    val = float(val)
                except ValueError:
                    pass

        cleaned[key] = val

    return cleaned


def extract_image_name(url: str) -> str:
    # Remove tag (after :)
    name_part = url.split(":")[0]
    # If / exists, take after last /, else take the whole name_part
    image_name = name_part.rsplit("/", 1)[-1]
    return image_name


def make_wait_command(cfg, depends_on=[]):
    return local.Command(
        "wait-for-vm-ready",
        create=f"""
          bash -c '
          for i in $(seq 1 60); do
            STATUS=$(gcloud compute instances describe {cfg['stack_name']} \
              --project {cfg["project"]} --zone {cfg["zone"]} --format="value(status)")
            echo "[INFO] VM status: $STATUS"
            if [ "$STATUS" = "RUNNING" ]; then
              exit 0
            fi
            sleep 5
          done
          echo "[ERROR] VM never reached RUNNING"
          exit 1
          '
        """,
        delete="echo '[SKIP] wait-for-vm-ready has no delete step' && exit 0",
        opts=ResourceOptions(depends_on=depends_on),
    )

def create_disk(disk_executions, depends_on=None):
    cache_disks = []

    if depends_on is None:
        depends_on = []

    for idx, snap in enumerate(disk_executions):
        retain = snap.get("retain_on_delete", False)

        opts = ResourceOptions(
            depends_on=depends_on,
            retain_on_delete=retain,
        )

        disk = compute.Disk(
            f"cacheDisk-{idx}",
            **snap["disk_args"],
            opts=opts,
        )
        cache_disks.append(disk)

    return cache_disks


def read_startup_file(cfg, service):
    # set predefined startup file
    if not 'startup_file' in cfg:
        # load the startup file
        startup_file_path = Path(cfg['gcp_script_path']) / f"startup_{service}.sh"
        cfg['startup_file'] = cfg.get('startup_file', startup_file_path)

    if not Path(cfg['startup_file']).is_absolute():
        cfg['startup_file'] = Path(cfg['gcp_script_path']) / cfg['startup_file']

    # load startup-script to automate inferencing engine deployment after VM ready
    startup_tmpl = Path(cfg['startup_file']).read_text()

    return startup_tmpl


def load_utils_script(cfg):
    """Load utils.sh and inject any optional installation scripts into startup_tmpl."""
    util_script_path = Path(cfg["gcp_script_path"]) / "utils.sh"
    util_tmpl = util_script_path.read_text().strip()

    return util_tmpl


def prepare_utils(cfg, matched_entry, startup_tmpl):
    """Load utils.sh and inject any optional installation scripts into startup_tmpl."""
    util_script_path = Path(cfg["gcp_script_path"]) / "utils.sh"
    util_tmpl = util_script_path.read_text().strip()

    # --- Collect additional installation snippets ---
    execution_script = ""
    if matched_entry:
        for script in matched_entry.get("installation", []):
            p = Path(cfg["gcp_script_path"]) / script
            if p.exists():
                content = p.read_text().rstrip() + "\n\n"
                execution_script += content
            else:
                pulumi.log.warn(f"Installation script not found: {p}")

    # Replace placeholder in startup template
    startup_tmpl = startup_tmpl.replace(
        "__EXECUTION_SCRIPT__",
        textwrap.dedent(f"""
        # === BEGIN EXECUTION ({cfg['service']}) ===
        {execution_script.strip()}
        # === END EXECUTION ===
        """).strip()
    )

    util_tmpl = textwrap.dedent(util_tmpl).strip()

    return util_tmpl, startup_tmpl

# --- Custom Dumper to handle multiline strings as literal blocks ---
def str_presenter(dumper, data):
    if "\n" in data:
        # Normalize indentation and ensure trailing newline → use '|'
        data = "\n".join(line.rstrip().replace("\t", "    ") for line in data.splitlines())
        if not data.endswith("\n"):
            data += "\n"
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


class LiteralDumper(yaml.SafeDumper):
    pass

def prepare_task_file(cfg, service):
    # get default
    taskfile_path = Path(cfg["gcp_script_path"]) / 'tasks' / f"Taskfile-{service}.yaml"

    # override if exists
    if cfg.get('task_file'):
        taskfile_path = Path(cfg["gcp_script_path"]) / cfg.get('task_file')

    base_yaml = taskfile_path.read_text()

    run_override = cfg.get("run")
    if not run_override:
        return Template(base_yaml)

    # Register for all str with our custom Dumper
    LiteralDumper.add_representer(str, str_presenter)

    # Parse YAML
    data = yaml.safe_load(base_yaml) or {}
    tasks = data.setdefault("tasks", {})
    run_task = tasks.get("run", {})

    # Preserve original desc if present
    desc = run_task.get("desc", "Run VLLM Docker")

    # This string will be handled by our str_presenter and become a `|` block
    tasks["run"] = {
        "desc": desc,
        "cmds": [run_override],
    }

    # Dump YAML to string
    buf = StringIO()
    yaml.dump(
        data,
        buf,
        Dumper=LiteralDumper,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    )

    # Get the actual string output
    yaml_text = buf.getvalue()

    return Template(yaml_text)


def prepare_proxy(cfg):
    if cfg.get("proxy", "").strip() != "":
        # PROXY NAT
        proxy_file = Path(cfg['gcp_script_path']) / "install_proxy.sh"
        if not proxy_file.exists():
            raise FileNotFoundError(f"Proxy config not found: {proxy_file}")

        raw_proxy = proxy_file.read_text()
        final_proxy = raw_proxy.replace("__PROXY__", cfg["proxy"])

        return final_proxy
    return ""


def inject_template(tmpl, default_task):
    task_file_filled = tmpl.substitute(**default_task)
    task_file_clean = textwrap.dedent(task_file_filled).strip()

    return task_file_clean