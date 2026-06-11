import os
import re
import json
import ast
import petname
import socket
import subprocess
import tempfile
import yaml
import time
import random

from pathlib import Path
from typing import Any, Callable

from functools import wraps

from google.oauth2 import service_account as gcp_service_account
from googleapiclient import discovery

from dagploy_dax.service_lib.parser import merged_profile

from googleapiclient.errors import HttpError
from hydra import compose, initialize_config_dir
from omegaconf import DictConfig, OmegaConf

from dotenv import dotenv_values

import pulumi.automation as auto


def project_root() -> Path:
    return Path(os.getenv("PROJECT_PATH", Path.cwd())).expanduser().resolve()

def load_app_env() -> dict[str, str]:
    env_path = project_root() / ".env"

    if not env_path.exists():
        return {}

    values = dotenv_values(env_path)

    for key, value in values.items():
        if value is not None:
            os.environ.setdefault(key, value)

    return {k: str(v) for k, v in values.items() if v is not None}


def load_project_path() -> Path:
    load_app_env()

    path = project_root()

    if not path.is_dir():
        raise FileNotFoundError(f"Project directory not found: {path}")

    return path


def load_config_path() -> Path:
    path = load_project_path() / "config"

    if not path.is_dir():
        raise FileNotFoundError(f"Config directory not found: {path}")

    return path

def load_pulumi_path() -> Path:
    path = load_project_path() / "pulumi_yaml"

    if not path.is_dir():
        raise FileNotFoundError(f"Pulumi directory not found: {path}")

    return path


def load_gcp_config_path() -> Path:
    path = load_project_path() / "gcp_vm_script"

    if not path.is_dir():
        raise FileNotFoundError(f"GCP directory not found: {path}")

    return path


def load_default_config():
    """Loads default config from PROJECT_PATH/config."""
    default_config_path = load_config_path()

    if not default_config_path.exists():
        raise FileNotFoundError(f"Config folder not found: {default_config_path}")

    with initialize_config_dir(config_dir=str(default_config_path), version_base=None):
        raw_cfg: DictConfig = compose(config_name="config")

    cfg = OmegaConf.to_container(raw_cfg.env, resolve=True)
    resources = OmegaConf.to_container(raw_cfg.resources, resolve=True)

    return cfg, resources



def read_required_file(path: Path) -> str:
    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    return path.read_text()


def prepare_proxy(cfg):
    """
    Load proxy from global proxy script
    """
    if cfg.get("proxy", "").strip() != "":
        # PROXY NAT
        proxy_file = Path(cfg['gcp_script_path']) / "install_proxy.sh"
        if not proxy_file.exists():
            raise FileNotFoundError(f"Proxy config not found: {proxy_file}")

        raw_proxy = proxy_file.read_text()
        final_proxy = raw_proxy.replace("__PROXY__", cfg["proxy"])

        return final_proxy
    return ""

def prepare_proxy_from_tool(merged):
    """
    Load proxy from local tool folder
    """
    if merged.get("proxy", "").strip() != "":

        # PROXY NAT
        raw_proxy = read_required_file(Path(merged['tools_dirpath']) / "install_proxy.sh")

        if raw_proxy.strip() == "":
            return ""

        # Replace with real proxy value from config
        final_proxy = raw_proxy.replace("__PROXY__", merged["proxyDefault"])

        return final_proxy

    return ""


def measure_duration(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        on_output = kwargs.get("on_output", print)
        start = time.time()
        result = func(*args, **kwargs)
        end = time.time()
        duration = int(round(end - start))
        minutes, seconds = divmod(duration, 60)
        msg = ""
        if minutes:
            msg = f"⏱ Total run time: {minutes} minutes {seconds} seconds"
        else:
            msg = f"⏱ Total run time: {seconds} seconds"
        on_output(msg)
        return {k: v for k, v in result.items()}
    return wrapper


def get_startup_logs(instance, zone, project, start_offset=0, port=1):
    import json
    import re
    import subprocess

    next_start = str(start_offset)

    cmd = [
        "gcloud", "compute", "instances", "get-serial-port-output",
        instance,
        f"--zone={zone}",
        f"--project={project}",
        f"--port={port}",
        f"--start={next_start}",
        "--format=json",
    ]

    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    if result.returncode != 0:
        print(f"Command failed with return code {result.returncode}: {result.stderr.strip()}")
        return [], next_start

    try:
        output = json.loads(result.stdout)
    except json.JSONDecodeError:
        print(f"Failed to parse JSON output: {result.stdout[:1000]}")
        return [], next_start

    contents = output.get("contents", "")
    next_start = str(output.get("next", next_start))

    normalized = contents.replace("\r\n", "\n").replace("\r", "\n")

    cleaned_lines = []
    for line in normalized.splitlines():
        line = re.sub(r"\x1B\[[0-?]*[ -/]*[@-~]", "", line)   # broader ANSI removal
        line = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", line)  # control chars
        line = re.sub(r"[ ]{2,}", " ", line).strip()

        if not line:
            continue

        cleaned_lines.append(line)

    return cleaned_lines, next_start

def running_in_gcp() -> bool:
    import urllib.request

    url = "http://metadata.google.internal/computeMetadata/v1/instance/id"
    req = urllib.request.Request(url, headers={"Metadata-Flavor": "Google"})

    try:
        with urllib.request.urlopen(req, timeout=0.5):
            return True
    except Exception:
        return False


def is_port_open(host, port, timeout=3):
    """
    Check if a TCP port is open — but ONLY when running inside GCP VPC.
    Outside GCP (local machine, GitHub Actions, CI, dev laptop):
    → Skip checking and return True immediately.
    """
    # Skip port probing when NOT inside GCP
    if not running_in_gcp():
        return True

    # Normal port test when inside GCP VM/VPC
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False

def get_vm_status(project, zone, instance):
    """Return the VM status (RUNNING, TERMINATED, STOPPED, SUSPENDED, NOT_FOUND, etc)."""
    creds = gcp_service_account.Credentials.from_service_account_file(
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"]
    )
    svc = discovery.build("compute", "v1", credentials=creds, cache_discovery=False)
    try:
        resp = svc.instances().get(project=project, zone=zone, instance=instance).execute()
        return resp.get("status", "UNKNOWN")
    except HttpError as e:
        if e.resp.status == 404:
            return "NOT_FOUND"
        raise

def _matches_any(line: str, patterns) -> bool:
    return any(p.search(line) for p in patterns)

def _log_tail(lines, tail=50):
    return lines[-tail:] if lines else []

STARTUP_PATTERNS = [
    re.compile(
        r'^.*?Metadata key\("startup-script"\), command\("/bin/bash"\):\s*(.*)$',
        re.IGNORECASE,
    ),
    re.compile(
        r'^.*?startup-script:\s*(.*)$',
        re.IGNORECASE,
    ),
]

def extract_startup_message(line: str) -> str | None:
    """
    Return only the useful startup-script message body.

    Supports both formats:

    1) Newer format:
       Apr 16 16:31:43 google_metadata_script_runner[1499]:
       Metadata key("startup-script"), command("/bin/bash"): [INFO] Changing ownership to dev:dev ...
       -> [INFO] Changing ownership to dev:dev ...

    2) Older format:
       Apr 18 15:57:25 google_metadata_script_runner[2465]: startup-script: ✅ GPU installation completed
       -> ✅ GPU installation completed
    """
    if not line:
        return None

    for pattern in STARTUP_PATTERNS:
        match = pattern.search(line)
        if match:
            message = match.group(1).strip()

            # Ignore prefix-only lines with no useful message
            if not message:
                return None

            return message

    return None


def load_boot_monitor_patterns() -> tuple[
    list[re.Pattern],
    list[re.Pattern],
    list[re.Pattern],
    list[re.Pattern],
]:
    """
    Load the regex patterns for monitoring GCP VM startup script logs
    from the boot_monitor.yaml file.
    """
    rule_file = Path(load_config_path()) / "boot_monitor.yaml"

    with rule_file.open("r", encoding="utf-8") as f:
        rules = yaml.safe_load(f) or {}

    def compile_list(name: str) -> list[re.Pattern]:
        return [
            re.compile(pattern, re.IGNORECASE)
            for pattern in rules.get(name, [])
        ]

    return (
        compile_list("start_patterns"),
        compile_list("fail_patterns"),
        compile_list("finish_patterns"),
        compile_list("ignore_patterns"),
    )


def wait_startup_script_status(
    instance_name: str,
    zone: str,
    project: str,
    max_wait: int = 1200,
    poll_interval: int = 10,
    warmup_delay: int = 30,
    on_output: callable = print,
) -> bool:
    """
    Wait for the GCP VM startup script to finish, capturing logs and detecting failures.
    """
    on_output(f"⏱ Waiting for GCP VM '{instance_name}' startup-script to finish…")

    start_patterns, fail_patterns, finish_patterns, ignore_patterns = (
        load_boot_monitor_patterns()
    )

    started = False
    next_start = 0
    start_time = time.time()
    processed_logs = set()

    if warmup_delay:
        time.sleep(warmup_delay)

    # for VLLM
    disable_fail_check = False

    with tempfile.NamedTemporaryFile(mode="w+", delete=False, suffix=".log") as log_file:
        on_output(f"[i] Full logs saved at: {log_file.name}")

        while True:
            time.sleep(poll_interval)
            on_output(f"🡢🡢🡢 Fetching...")

            if time.time() - start_time >= max_wait:
                raise TimeoutError(f"Timeout waiting for startup-script. Check {log_file.name}")

            status = get_vm_status(project, zone, instance_name)
            if status in ["TERMINATED", "STOPPED", "SUSPENDED", "NOT_FOUND"]:
                raise RuntimeError(f"VM stopped (status: {status}) before script completed")

            logs, next_start = get_startup_logs(instance_name, zone, project, next_start)

            if not logs:
                continue

            for line in logs:
                clean = line.strip()
                if not clean:
                    continue

                # --- Only begin parsing after explicit marker ---
                if not started and any(p.search(clean) for p in start_patterns):
                    started = True
                    on_output("✓ Startup-script marker detected (STARTUP_SCRIPT_START).")
                    continue

                if not started:
                    continue  # ignore all logs before STARTUP_SCRIPT_START

                # --- Ignore unrelated system/service logs ---
                if any(p.search(clean) for p in ignore_patterns):
                    continue

                # Patch for VLLM Value error: "Chunked prefill", disable failure checks afterwards
                if "chunked prefill" in clean.lower():
                    disable_fail_check = True

                # Detect failure
                if not disable_fail_check and any(p.search(clean) for p in fail_patterns):
                    raise RuntimeError(f"❌ Startup script failed: {clean}")

                # Detect success
                if any(p.search(clean) for p in finish_patterns):
                    on_output(f"✅ Startup script finished successfully.")
                    log_file.write(clean + "\n")
                    log_file.flush()

                    return True

                # Now mark processed
                if clean in processed_logs:
                    continue

                processed_logs.add(clean)

                # Extract useful startup message
                message = extract_startup_message(clean)

                # Write log
                log_file.write(clean + "\n")
                log_file.flush()

                if message:
                    on_output(message)


def wait_until_llm_ready(
    instance_name: str,
    vm_ip: str,
    port: int,
    zone: str,
    project: str,
    max_wait: int = 900,
    poll_interval: int = 5,
    warmup_delay: int = 30,
    on_output: Callable[[str], None] = print
) -> bool:
    on_output(f"⏱ Waiting LLM setup '{instance_name}' startup-script to finish…")

    if not wait_startup_script_status(instance_name, zone, project, max_wait, poll_interval, warmup_delay, on_output):
        return False

    # Now wait for port to open
    start_time = time.time()
    while time.time() - start_time < max_wait:
        if is_port_open(vm_ip, port):
            on_output(f"✅ Model is ready at http://{vm_ip}:{port}")
            return True
        time.sleep(poll_interval)

    raise TimeoutError(f"Port {port} not open on {vm_ip} after {max_wait} seconds.")


def wait_for_vm_termination(project, zone, instance, poll_interval=25, on_output=print):
    """
    Block until the VM is TERMINATED, STOPPED, SUSPENDED, or deleted (404).
    """
    while True:
        status = get_vm_status(project, zone, instance)

        if status in ["TERMINATED", "STOPPED", "SUSPENDED"]:
            on_output(f"✅ VM status: {status}.")
            return

        if status == "NOT_FOUND":
            on_output("✅ Instance no longer found (likely deleted). Done.")
            return

        on_output(f"VM status: {status}")

        # jitter to avoid thundering herd
        time.sleep(poll_interval + random.uniform(0, 3))


def check_config_file(config):
    if not os.path.exists(config['config_path']):
        raise ValueError(f"Path {config['config_path']} is not exists.")

    task_file = config.get("taskFile", None)
    if task_file:
        if not Path(task_file).is_absolute():
            task_file = Path(config['config_path']) / task_file

        if not os.path.exists(task_file):
            raise ValueError(f"Path {task_file} does not exist.")

    startup_file = config.get("startupFile", None)
    if startup_file:
        if not Path(startup_file).is_absolute():
            startup_file = Path(config['config_path']) / startup_file

        if not os.path.exists(startup_file):
            raise ValueError(f"Path {startup_file} is not exists.")

    config_file = config.get("configFile", None)
    if config_file:
        if not Path(config_file).is_absolute():
            config_file = Path(config['config_path']) / config_file

        if not os.path.exists(config_file):
            raise ValueError(f"Path {config_file} is not exists.")


def modify_service_name(service: str, limit=20) -> str:
    service = service.lower().replace("_", "-")
    cleaned = re.sub(r'[^a-z0-9-]', '', service)

    # If user passes 0, do not limit
    result = cleaned if limit in (None, 0) else cleaned[:limit]

    if result and limit > 0:
        result += '-'

    return result

def generate_safe_petname():
    while True:
        name = petname.Generate(1)
        if not any(bad in name for bad in ['pig', 'maggot']):
            return name


def generate_stack_name(service: str, limit: int = 16) -> str:
    words = re.split(r"[_-]+", service.lower())

    words = [
        re.sub(r"[^a-z0-9]", "", word)
        for word in words
        if word.strip()
    ]

    if len(words) >= 2:
        cleaned = "-".join(words[:2])
    elif words:
        cleaned = words[0]
    else:
        cleaned = "stack"

    cleaned = cleaned[:limit].strip("-")

    rand_suffix = generate_safe_petname()

    return f"{cleaned}-{rand_suffix}"


def configure_pulumi_config(cfg, stack_name, infra_yaml_path, service, profile=None, resources=None):
    project = cfg.get("gcp:project")
    pulumi_project_name = cfg.get("gcp:project")
    service_account = cfg.get("gcp:serviceAccount")
    service_account_key = cfg.get("gcp:serviceAccountKey")

    config = merged_profile(cfg, service, profile, resources)

    if not stack_name:
        rand_suffix = generate_safe_petname()
        stack_name = f"{modify_service_name(service)}{rand_suffix}"
    else:
        stack_name = modify_service_name(stack_name, 0)

    # Setup default configuration including the script path
    default_config = {
        "env": cfg.get('name'),
        "config_path": load_config_path(),
        "gcp_script_path": load_gcp_config_path(),
        "infra_yaml_path": infra_yaml_path,
        "vmName": stack_name,
        "project": project,
        "project_name": pulumi_project_name,
        "serviceAccount": service_account,
        "serviceAccountKey": service_account_key,
        "stackName": stack_name
    }

    # Generate default values for future use in executor
    # This is the default variable setup from gcp
    # To load as default, add this
    DEFAULT_KEYS = {
        "networkPublicDefault": ("gcp:networkPublic", ""),
        "networkDefault": ("gcp:network", ""),
        "urlMapDefault": ("gcp:urlMap", ""),
        "dnsZoneDefault": ("gcp:dnsZone", ""),
        "dnsZonePublicDefault": ("gcp:dnsZonePublic", ""),
        "maxWaitDefault": ("gcp:maxWait", ""),
        "oauthClientDefault": ("gcp:oauthClient", ""),
        "oauthSecretDefault": ("gcp:oauthSecret", ""),
        "hfTokenDefault": ("gcp:hfToken", ""),
        "proxyDefault": ("gcp:proxy", ""),
        "iapUserDefault": ("gcp:iapUser", "[]"),
        "healthcheckPathDefault": ("gcp:healthcheckPath", ""),
        "healthcheckPortDefault": ("gcp:healthcheckPort", ""),
        "daxApiDefault": ("gcp:daxApi", ""),
        "daxGitSecretDefault": ("gcp:daxGitSecret", ""),
        "daxGitURLDefault": ("gcp:daxGitURL", ""),
        "osLoginDefault": ("gcp:osLogin", ""),
        "iapLoginDefault": ("gcp:iapLogin", ""),
        "dockerRunDefault": ("gcp:dockerRun", ""),
        "errorDestroyDefault": ("gcp:errorDestroy", ""),
    }

    # Auto-fill default GCP keys
    for out_key, (in_key, fallback) in DEFAULT_KEYS.items():
        default_config[out_key] = cfg.get(in_key, fallback)

    # check configuration file
    check_config_file(default_config)

    return default_config, config


def check_stack(stack_name: str, project_name: str, work_dir: str, pulumi_program, on_output):
    try:
        # Try selecting an existing stack
        stack = auto.select_stack(
            stack_name=stack_name,
            project_name=project_name,
            work_dir=work_dir,
            program=pulumi_program,
        )
        outputs = stack.outputs()
        on_output(f"🔎 Found existing stack '{stack_name}', returning outputs...")

        return outputs, stack, True

    except auto.StackNotFoundError:
        # Stack does not exist → create new one
        on_output(f"🆕 Stack '{stack_name}' not found, creating new stack...")
        stack = auto.create_stack(
            stack_name=stack_name,
            project_name=project_name,
            work_dir=work_dir,
            program=pulumi_program,
        )

        return None, stack, False


def execute(cfg, merged, job_id, update_state, deploy_fn, provisioning_function_dir, launch_config, on_output):
    safe_merged = dict(merged)

    # Handler for run taskfile inside YAML
    # images:
    #    - us-west1-b
    #    - us-west1-c
    # run: |
    #    docker run --gpus all --name vllm \
    #               --volume /usr/lo ..

    # Make sure the run is not making the OmegaConf break
    run_raw = None
    if "run" in safe_merged and isinstance(safe_merged["run"], str):
        run_raw = safe_merged["run"]
        del safe_merged["run"]  # remove it so OmegaConf never sees it

    merged_native = OmegaConf.to_container(OmegaConf.create(safe_merged), resolve=True)

    if run_raw is not None:
        merged_native["run"] = run_raw

    # check startup, taskfile and others is exists
    check_config_file(merged_native)

    if update_state:
        update_state({'stack_name': merged_native['stackName'], 'config': merged_native})

    # idempotent checker for stack
    try:
        # Try selecting an existing stack
        stack = auto.select_stack(
            stack_name=merged['stackName'],
            project_name=merged['project'],
            work_dir=provisioning_function_dir,
            program=deploy_fn,
        )
    except auto.StackNotFoundError:
        pass
    else:
        on_output(f'⚠️ Stack {merged["stackName"]} already exists. Stop provisioning ...')
        return {"error": f"{merged['stackName']} already exists. Can't duplicate", 'job_id': job_id}

    # Create or select the Pulumi stack
    stack = auto.create_or_select_stack(
        stack_name=merged['stackName'],
        project_name=merged['project'],
        program=deploy_fn,
        opts=auto.LocalWorkspaceOptions(work_dir=provisioning_function_dir),
    )

    try:
        # In execute():
        launch_obj = launch_config(
            cfg=cfg,
            work_dir=provisioning_function_dir,
            merged=merged_native,
            stack=stack,
            deploy_fn=deploy_fn,
            on_output=on_output,
        )

        # Then call it explicitly
        pulumi_result = launch_obj()

    except Exception as e:
        on_output(f"EXECUTE - Error launch object {merged['stackName']}")
        return {"error": f"{e}", 'job_id': job_id}

    if not getattr(pulumi_result, "outputs", None):
        on_output(f'EXECUTE: OUTPUT is None: {pulumi_result}')
        raise ValueError("Error during deployment: No outputs returned from Pulumi. Check logs for details.")

    try:
        result = {k: v.value for k, v in pulumi_result.outputs.items()}
    except Exception as e:
        on_output(f'EXECUTE - FAILED ITERATE {pulumi_result}')
        # If pulumi failed due to resources not found, then return empty error
        return {"error": f"EXECUTE : {e}", 'job_id': job_id}

    result['job_id'] = job_id

    return result


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


def try_stack_up(stack, zone, on_output):
    stack.set_config("gcp:zone", auto.ConfigValue(value=zone))
    try:
        result = stack.up(on_output=on_output, parallel=4)
        return result, zone
    except Exception as e:
        if "does not have enough resources available" in str(e) or "is currently unavailable" in str(e):
            print(f"Zone {zone} unavailable: {e}")
            return None, zone
        else:
            raise ValueError(e)

def is_valid_gcs_url(url: str) -> bool:
    """
    Validates if the given string is a valid Google Cloud Storage URL.
    Example: gs://my-bucket-name/path/to/file.txt
    """
    # Regex: gs://<bucket>[/<path>...]
    # Bucket naming rules: lowercase letters, numbers, dashes, underscores, dots
    # Can't start/end with dash or dot, 3-63 chars
    gcs_pattern = re.compile(
        r"^gs://"
        r"(?P<bucket>[a-z0-9][a-z0-9._-]{1,61}[a-z0-9])"  # bucket
        r"(/(?P<object>.*))?$",  # optional object path
        re.IGNORECASE
    )
    return bool(gcs_pattern.match(url))


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


def validate_docker_urls(docker_images: list) -> list[str]:
    """
    Validate a comma-separated string of Docker image URLs.

    Supports:
      - dockerhub images (e.g. python:3.11, ubuntu, nginx:latest)
      - private registries (e.g. us-docker.pkg.dev/xxt/repo/image:tag)

    Returns a list of valid image URLs. Raises ValueError if any invalid.
    """

    if not docker_images:
        raise ValueError("docker_images is empty")

    # Docker image format regex
    docker_regex = re.compile(
        r"""^
        (?:                                     # optional registry prefix
            [a-zA-Z0-9.-]+(?::[0-9]+)?          # registry host[:port]
            (?:/[a-z0-9._-]+)+                  # one or more repo parts
        |
            [a-z0-9._-]+(?:/[a-z0-9._-]+)*      # plain image name
        )
        (?::[a-zA-Z0-9._-]+)?                   # optional :tag
        (?:@[A-Za-z0-9:+=_-]+)?                 # optional @digest
        $""",
        re.VERBOSE,
    )

    invalid = [u for u in docker_images if not docker_regex.match(u)]
    if invalid:
        raise ValueError(f"Invalid Docker image(s): {', '.join(invalid)}")

    return docker_images


def emit_state(
    update_state: Callable[..., Any] | None,
    status: str,
    step: str,
    **payload: Any,
) -> None:
    if update_state:
        update_state(status=status, step=step, **payload)