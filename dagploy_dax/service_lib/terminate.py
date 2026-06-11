import os

import time, random, re
from typing import Callable

import shutil
import subprocess
from pathlib import Path

from rich import print

import pulumi.automation as auto
from hydra import initialize, compose
from omegaconf import DictConfig

from dagploy_dax.service_lib.utils import load_pulumi_path, measure_duration

PULUMI_PROGRAM_DIR = str(load_pulumi_path().resolve())

# RETRIEVE ENVIRONMENT VARIABLES
PROJECT_NAME = os.environ.get("GOOGLE_CLOUD_PROJECT")
ZONE = os.environ.get("GOOGLE_CLOUD_ZONE")
SERVICE_ACCOUNT = os.environ.get("GOOGLE_CLOUD_SERVICE_ACCOUNT")


def reset_pulumi_local_login(state_dir, on_output=print):
    # Run: pulumi login file://./infra
    cmd = ["pulumi", "login", f"file://{state_dir}"]
    on_output(f"⩥ Running: {' '.join(cmd)}")

    try:
        subprocess.run(cmd, check=True)
        on_output(f"✅ Pulumi now using local backend at {state_dir}")
    except subprocess.CalledProcessError as e:
        on_output(f"❌ Pulumi login failed: {e}")

def destroy_all_stacks(project_name, work_dir, program=None, on_output=print):
    ws = auto.LocalWorkspace(
        project_settings=auto.ProjectSettings(name=project_name, runtime="python"),
        work_dir=str(work_dir)
    )
    destroyed = []

    summaries = ws.list_stacks()  # List[StackSummary]
    on_output(f"✧ Found {len(summaries)} stacks.")

    for _stack in ws.list_stacks():
        stack_name = _stack.name
        on_output(f"🠶 Selected stack: {stack_name}")

        stack = get_stack(stack_name, PROJECT_NAME, PULUMI_PROGRAM_DIR, None)
        stack.destroy(on_output=on_output, suppress_outputs=True)
        stack.workspace.remove_stack(stack_name)

        destroyed.append(stack_name)

    return destroyed


def reset_stacks(
    on_output: callable = print
):
    """
    Destroys all stacks, removes them from Pulumi workspace, and resets Pulumi local state.
    """
    pulumi_home = Path.home() / ".pulumi"
    if not pulumi_home.exists():
        on_output(f"No Pulumi installation found on ~/.pulumi/bin")
        return {"status": "success"}

    if not os.path.isdir(PULUMI_PROGRAM_DIR):
        on_output(f"PULUMI_PROGRAM_DIR '{PULUMI_PROGRAM_DIR}' does not exist.")
        return {"status": "success"}

    pulumi_yaml_path = os.path.join(PULUMI_PROGRAM_DIR, "Pulumi.yaml")
    if not os.path.exists(pulumi_yaml_path) or not os.path.isfile(pulumi_yaml_path):
        on_output(f"Pulumi.yaml not found")
        return {"status": "success"}

    output_stacks = destroy_all_stacks(PROJECT_NAME, PULUMI_PROGRAM_DIR, program=None, on_output=on_output)

    # Remove state .pulumi
    pulumi_state = str(Path(PULUMI_PROGRAM_DIR) / ".pulumi")
    if os.path.exists(pulumi_state) and os.path.isfile(pulumi_state):
        on_output(f"🧹 Removing .pulumi state at {pulumi_state} ...")
        shutil.rmtree(pulumi_state)
        on_output("Pulumi state removed.")

        # Ensure Pulumi CLI is logged in to local backend
        reset_pulumi_local_login(state_dir=PULUMI_PROGRAM_DIR, on_output=on_output)

    on_output(f"✅ [bold green] {len(output_stacks)} stacks destroyed, removed, and Pulumi state reset.[/bold green]")

    return {"status": "success"}


def get_stack(stack_name, project_name, work_dir, program=None):
    try:
        stack = auto.select_stack(
            stack_name=stack_name,
            project_name=project_name,
            program=program or (lambda: None),
            opts=auto.LocalWorkspaceOptions(work_dir=work_dir,
                                            env_vars={
                                                "PULUMI_K8S_DELETE_UNREACHABLE": "true",
                                                }
                                            ),
        )
    except Exception as e:
        raise ValueError(e)

    return stack


@measure_duration
def cleanup_stack(
    stack_name: str,
    on_output: Callable[[str], None] = print,
):
    try:
        stack = get_stack(stack_name, PROJECT_NAME, PULUMI_PROGRAM_DIR, None)
    except Exception as e:
        on_output(f"❎ [bold red]Error selecting stack '{stack_name}': {e}[/bold red]")
        raise

    on_output(f"🏼 Selected stack: {stack_name}")

    # cancel manually
    try:
        subprocess.run(
            ["pulumi", "stack", "select", stack_name],
            cwd=PULUMI_PROGRAM_DIR,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        subprocess.run(
            ["pulumi", "cancel"],
            cwd=PULUMI_PROGRAM_DIR,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        on_output("✅ [green]Pulumi cancel issued; retrying destroy…[/green]")
    except subprocess.CalledProcessError as sp_err:
        err = (sp_err.stderr or sp_err.stdout).strip()
        on_output(f"❌ [red]Pulumi CLI cancel failed:[/red] {err}")

    #
    # # Cancel any in‑flight update
    # try:
    #     on_output("🏼 Cancelling stack provisioning …")
    #     cancel_res = stack.cancel()
    # except Exception as e:
    #     cancel_res = f"Cancel error: {e}"
    #     on_output(f"🏼 [yellow]Cancel error (perhaps no update to cancel): {e}[/yellow]")
    
    # Destroy all resources (with lock handling)
    destroy_res = None

    try:
        on_output("🏼 Destroying resources…")
        destroy_res = stack.destroy(on_output=on_output, suppress_outputs=True, target_dependents=True)
    except Exception as e:
        msg = str(e)
        if re.search(r'Error\s*404', msg) or 'not found' in msg.lower():
            destroy_res = f"Warning: resource already deleted externally: {msg}"
            on_output(f"🏼 [yellow]Resource already deleted externally: {msg}[/yellow]")
        else:
            on_output(f"❎ [bold red]Destroy error: {e}[/bold red]")
            raise

    # Refresh state
    try:
        on_output("⥁ Refreshing stack state…")
        refresh_res = stack.refresh(on_output=on_output)
    except Exception as e:
        refresh_res = f"Refresh error: {e}"
        on_output(f"❎ [bold red]Refresh error: {e}[/bold red]")

    #  Remove from workspace
    try:
        on_output("🏼 Removing stack from workspace …")
        auto.LocalWorkspace(PULUMI_PROGRAM_DIR).remove_stack(stack_name)
        remove_res = "Removed successfully"
        on_output(f"🏼 Stack '{stack_name}' removed")
    except Exception as e:
        remove_res = f"Remove error: {e}"
        on_output(f"❎ [bold red]Failed to remove stack: {e}[/bold red]")

    on_output("✅ Cleanup complete.")
    return {
        "destroy": destroy_res,
        "refresh": refresh_res,
        "remove": remove_res,
    }

@measure_duration
def remove_urlmap(stack_name: str, on_output: Callable[[str], None] = print):
    try:
        stack = get_stack(stack_name, PROJECT_NAME, PULUMI_PROGRAM_DIR, None)
    except Exception as e:
        on_output(f"❎ [bold red]Error selecting stack '{stack_name}': {e}[/bold red]")
        raise

    on_output(f"🟢 Selected stack: {stack_name}")

    def run(cmd: list[str], check: bool = True):
        """
        Run a pulumi command and stream output line-by-line
        """
        proc = subprocess.Popen(
            cmd,
            cwd=PULUMI_PROGRAM_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        output_lines = []
        for line in proc.stdout:
            line = line.rstrip()
            output_lines.append(line)
            on_output(line)

        rc = proc.wait()
        if check and rc != 0:
            raise RuntimeError("\n".join(output_lines))

        return output_lines

    # ------------------------------------------------------------------
    # 1. Ensure stack is selected
    # ------------------------------------------------------------------
    try:
        run(["pulumi", "stack", "select", stack_name])
    except Exception as e:
        on_output(f"❌ [red]Failed to select stack:[/red] {e}")
        raise

    # ------------------------------------------------------------------
    # 2. Find dangling urlmap-upsert Command URNs
    # ------------------------------------------------------------------
    on_output("🔍 Scanning for dangling urlmap-upsert resources…")

    try:
        urn_lines = run(["pulumi", "stack", "--show-urns"], check=False)
    except Exception as e:
        on_output(f"❌ [red]Failed to list stack URNs:[/red] {e}")
        raise

    urlmap_urns: list[str] = []
    for line in urn_lines:
        if "command:local:Command::urlmap-upsert" in line:
            urn = line.split()[0]
            urlmap_urns.append(urn)

    if not urlmap_urns:
        on_output("✅ No dangling urlmap-upsert Command resources found")
    else:
        for urn in urlmap_urns:
            on_output(f"🧹 Removing stuck state: {urn}")
            try:
                run(["pulumi", "state", "delete", urn, "--yes"])
            except Exception as e:
                on_output(f"⚠️ [yellow]Failed to delete {urn} (continuing):[/yellow] {e}")

    # ------------------------------------------------------------------
    # 3. Cancel any in-progress Pulumi operation
    # ------------------------------------------------------------------
    on_output("🛑 Issuing pulumi cancel…")

    try:
        run(["pulumi", "cancel", "--yes"], check=False)
        on_output("✅ [green]Pulumi cancel completed[/green]")
    except Exception as e:
        # cancel is best-effort; do not fail cleanup
        on_output(f"⚠️ [yellow]Pulumi cancel failed (ignored):[/yellow] {e}")

    on_output("🎉 Stack cleanup finished — safe to retry destroy/up")


@measure_duration
def destroy_stack(stack_name: str = None, destroy_all: bool = False,
                  is_cluster: bool = False,
                  on_output: callable = print):
    """
    Shutdown stack resources safely, handling Pulumi locks automatically.
    """
    if not stack_name and not destroy_all:
        raise ValueError("⚠️ Provide stack name or set destroy_all=True")

    if stack_name:
        on_output(f"🠶 Selected stack: {stack_name} for project '{PROJECT_NAME}' at '{PULUMI_PROGRAM_DIR}'")

        try:
            stack = get_stack(stack_name, PROJECT_NAME, PULUMI_PROGRAM_DIR, None)
        except Exception as e:
            on_output(f"❎ [bold red]Failed to load stack: {e}[/bold red]")
            raise

        try:
            stack.destroy(on_output=on_output, suppress_outputs=True, target_dependents=True)

        except auto.CommandError as e:
            # Detect Pulumi lock issue
            if "currently locked" in str(e):
                on_output("⚠️ Stack is currently locked. Attempting to cancel lock...")

                # Determine path: parent.parent + 'infra/yaml'
                current_dir = Path(__file__).resolve().parent
                cancel_dir = current_dir.parent.parent / "pulumi_yaml"

                if not cancel_dir.exists():
                    on_output(f"❎ Cancel directory not found: {cancel_dir}")
                    raise

                try:
                    subprocess.run(
                        ["pulumi", "cancel", "--yes", "--quiet"],
                        cwd=cancel_dir,
                        check=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE
                    )
                    on_output("✅ Pulumi lock canceled successfully. Retrying destroy...")

                    # Retry destroy once
                    stack.destroy(on_output=on_output, suppress_outputs=True, target_dependents=True)

                except subprocess.CalledProcessError as cancel_err:
                    on_output(f"❌ Failed to cancel Pulumi lock: {cancel_err.stderr.decode().strip()}")
                    raise

            else:
                # Other Pulumi errors
                on_output(f"❌ Pulumi destroy failed: {e}")
                raise

        # Cleanup
        stack.workspace.remove_stack(stack_name)
        on_output(f"🗑️ Stack {stack_name} removed from workspace.")
        return {"status": "success", "result": stack}

    # destroy_all path
    output_stacks = destroy_all_stacks(PROJECT_NAME, PULUMI_PROGRAM_DIR, program=None, on_output=on_output)
    return {"status": "success", "result": output_stacks}


def pulumi_destroy_unavailable_resource(stack_name, project_name, work_dir, program,
                         on_output: Callable[[str], None] = print):
    stack = auto.create_or_select_stack(
        stack_name=stack_name,
        project_name=project_name,
        program=program,
        opts=auto.LocalWorkspaceOptions(work_dir=work_dir,
                                        env_vars={"PULUMI_K8S_DELETE_UNREACHABLE": "true"}),
    )

    try:
        stack.destroy(on_output=on_output, suppress_outputs=True, target_dependents=True)
    except Exception as e:
        on_output(f"[WARN] Pulumi destroy failed: {e}")


def pulumi_destroy_stack(stack_name, project_name, work_dir, program,
                         on_output: Callable[[str], None] = print):
    stack = auto.create_or_select_stack(
        stack_name=stack_name,
        project_name=project_name,
        program=program,
        opts=auto.LocalWorkspaceOptions(work_dir=work_dir,
                                        env_vars={
                                            "PULUMI_K8S_DELETE_UNREACHABLE": "true",  # 👈 key line
                                            }
                                        ),
    )

    stack.destroy(on_output=on_output, suppress_outputs=True, target_dependents=True)
    stack.workspace.remove_stack(stack_name)
