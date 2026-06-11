import time
import pulumi.automation as auto

from dagploy_dax.service_lib.utils import (
    convert_strings_to_list,
    wait_startup_script_status,
    is_port_open,
)
from dagploy_dax.service_lib.terminate import pulumi_destroy_stack


RESOURCE_UNAVAILABLE_MARKERS = (
    "zone_resource_pool_exhausted",
    "zone_resource_pool_exhausted_with_details",
    "does not have enough resources available",
    "resource pool exhausted",
    "resources are currently unavailable",
    "is currently unavailable",
    "not currently available in the zone",
    "try a different zone",
    "does not exist in zone",
)


def is_resource_unavailable_error(exc: Exception) -> bool:
    text = "\n".join(
        str(x)
        for x in (
            exc,
            getattr(exc, "stdout", ""),
            getattr(exc, "stderr", ""),
            getattr(exc, "message", ""),
        )
        if x
    ).lower()

    return any(marker in text for marker in RESOURCE_UNAVAILABLE_MARKERS)


def unwrap_output(value):
    return value.value if hasattr(value, "value") else value


class LaunchGPUInference:
    def __init__(self, cfg, work_dir, merged, stack, deploy_fn, on_output):
        self.cfg = cfg
        self.work_dir = work_dir
        self.merged = merged
        self.stack = stack
        self.deploy_fn = deploy_fn
        self.on_output = on_output

        # Default keeps current behavior
        self.destroy_on_error = bool(
            self.merged.get("error_destroy", True)
        )

    def destroy(self, stack_name: str) -> None:
        pulumi_destroy_stack(
            stack_name,
            self.stack,
            self.work_dir,
            self.deploy_fn,
            self.on_output,
        )

    def ensure_stack(self, stack_name: str) -> None:
        if self.stack is not None:
            self.on_output("🏽 Stack is found!")
            return

        self.on_output("⚠️ Stack instance is None, recreating...")

        self.stack = auto.create_or_select_stack(
            stack_name=stack_name,
            project_name=self.merged["project"],
            program=self.deploy_fn,
            opts=auto.LocalWorkspaceOptions(work_dir=self.work_dir),
        )

    def config_map(self) -> dict[str, auto.ConfigValue]:
        return {
            k: auto.ConfigValue(value=str(v))
            for k, v in self.merged.items()
        }

    def zones(self) -> list[str]:
        zones = [self.merged.get("zone")]
        zones += convert_strings_to_list(self.merged.get("alternativeZones")) or []
        return list(dict.fromkeys(z for z in zones if z))

    def cleanup_and_raise(
        self,
        stack_name: str,
        message: str,
        exc: Exception | None = None,
    ):
        if self.destroy_on_error:
            try:
                self.on_output(
                    f"🧹 Error detected. Destroying stack '{stack_name}' because destroyOnError=true"
                )
                self.destroy(stack_name)
            except Exception as destroy_error:
                self.on_output(f"⚠️ Cleanup failed: {destroy_error}")
        else:
            self.on_output(
                f"⚠️ Error detected, but stack '{stack_name}' was not destroyed because destroyOnError=false"
            )

        raise ValueError(message) from exc

    def log_config(self) -> None:
        hidden = ["id", "token", "secret", "user", "account", "default"]

        self.on_output(" ⌨  Run configuration ...")

        for k, v in self.merged.items():
            if not any(x in k.lower() for x in hidden):
                self.on_output(f"[cyan]⍚[/cyan] [bold]{k}: [green]{v}[/green][/bold]")

    def wait_port(self, host: str, port: int, max_wait: int, interval: int = 10) -> bool:
        deadline = time.time() + max_wait

        while time.time() < deadline:
            if is_port_open(host, port):
                return True

            time.sleep(interval)

        return False

    def launch_in_available_zone(self, stack_name: str):
        zones = self.zones()

        if not zones:
            raise ValueError("No zone configured.")

        self.on_output(f"Alternative zones: {zones}")

        last_error: Exception | None = None

        for idx, zone in enumerate(zones, start=1):
            self.merged["zone"] = zone
            self.on_output(f"⟳ Attempting zone {idx}/{len(zones)}: {zone}")

            try:
                self.stack.set_all_config(self.config_map())
                result = self.stack.up(on_output=self.on_output, parallel=4)
                self.on_output(f"✅ [bold green]Provision succeeded in zone {zone}![/bold green]")
                return result

            except Exception as e:
                last_error = e
                self.on_output(f"⤫ Provision failed in zone {zone}: {e}")

                if is_resource_unavailable_error(e):
                    self.on_output(f"⤫ GPU/resource unavailable in {zone}. Trying next zone...")
                    continue

                self.cleanup_and_raise(
                    stack_name,
                    f"Non-retryable provisioning error: {e}",
                    e,
                )

        self.cleanup_and_raise(
            stack_name,
            f"Failed after trying all zones: {zones}. Last error: {last_error}",
            last_error,
        )

    def validate_outputs(self, stack_name: str, result):
        outs = result.outputs or {}

        if not outs:
            self.cleanup_and_raise(
                stack_name,
                "Pulumi succeeded but returned no outputs.",
            )

        vm_name = unwrap_output(outs.get("vm_name"))
        external_ip = unwrap_output(outs.get("external_ip"))
        internal_ip = unwrap_output(outs.get("private_ip"))
        iap_domain = unwrap_output(outs.get("iap_domain")) or ""

        if not vm_name:
            self.cleanup_and_raise(
                stack_name,
                "Pulumi output missing required 'vm_name'.",
            )

        vm_ip = external_ip or internal_ip

        if not vm_ip:
            self.cleanup_and_raise(
                stack_name,
                "Pulumi output missing both 'external_ip' and 'private_ip'.",
            )

        return outs, vm_name, vm_ip, iap_domain

    def wait_startup(self, stack_name: str, vm_name: str, max_wait: int) -> None:
        self.on_output("[green]🏼 Run startup-scripts ...[/green]")

        try:
            startup_ok = wait_startup_script_status(
                vm_name,
                self.merged["zone"],
                self.merged["project"],
                max_wait,
                10,
                10,
                on_output=self.on_output,
            )
        except Exception as e:
            self.cleanup_and_raise(
                stack_name,
                f"Startup script check failed: {e}",
                e,
            )

        if not startup_ok:
            self.cleanup_and_raise(
                stack_name,
                f"Startup script failed for VM '{vm_name}'.",
            )

    def wait_model_port(
        self,
        stack_name: str,
        vm_ip: str,
        port: int,
        max_wait: int,
    ) -> None:
        if self.wait_port(vm_ip, port, max_wait):
            self.on_output(f"✅ Model is ready at http://{vm_ip}:{port}")
            return

        self.cleanup_and_raise(
            stack_name,
            f"Model port did not open within {max_wait}s: {vm_ip}:{port}",
        )

    def __call__(self):
        stack_name = self.merged["stackName"]

        self.ensure_stack(stack_name)

        try:
            self.stack.set_all_config(self.config_map())
        except Exception as e:
            raise ValueError(f"ERROR applying stack.set_all_config() on {self.stack}: {e}") from e

        self.log_config()
        self.on_output("🚀 Launching ...")

        result = self.launch_in_available_zone(stack_name)
        outs, vm_name, vm_ip, iap_domain = self.validate_outputs(stack_name, result)

        port = int(self.merged.get("port", 8000))
        max_wait = int(self.merged.get("maxWait", 900))

        self.wait_startup(stack_name, vm_name, max_wait)

        self.on_output(f"🏼 OUTPUT: {outs}\n")
        self.on_output(f"🚀 [bold green]VM IP: http://{vm_ip}[/bold green]")
        self.on_output(f"🚀 [bold green]Model {stack_name} is up: http://{vm_ip}:{port}[/bold green]")
        self.on_output(f"💬 [bold green]Try it at: http://{vm_ip}:8080[/bold green]")

        if iap_domain:
            self.on_output(f"💬 [bold green]Domain: {iap_domain}[/bold green]")

        self.wait_model_port(stack_name, vm_ip, port, max_wait)

        return result