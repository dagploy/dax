import pulumi.automation as auto

from dagploy_dax.service_lib.utils import (
    wait_startup_script_status,
    convert_strings_to_list,
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
    "quota",
    "exceeded",
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


class LaunchGPU:
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

    def _stack_has_resources(self) -> bool:
        try:
            return len(self.stack.export_stack().deployment.get("resources", [])) > 1
        except Exception:
            return False

    def _ensure_stack(self, stack_name: str) -> None:
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

    def _zones(self) -> list[str]:
        zones = [self.merged.get("zone")]
        zones += convert_strings_to_list(self.merged.get("alternativeZones")) or []
        return list(dict.fromkeys(z for z in zones if z))

    def _config_map(self) -> dict[str, auto.ConfigValue]:
        return {
            k: auto.ConfigValue(value=str(v))
            for k, v in self.merged.items()
        }

    def _log_config(self) -> None:
        hidden = {
            "id",
            "token",
            "secret",
            "user",
            "account",
            "default",
            "tool_script_b64",
        }

        self.on_output("⌨  Run configuration ...")

        for k, v in self.merged.items():
            if not any(x in k.lower() for x in hidden):
                self.on_output(f"⍚ {k}: {v}")

    def _cleanup_and_raise(
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

    def _launch_in_available_zone(self, stack_name: str):
        zones = self._zones()

        if not zones:
            raise ValueError("No zone configured.")

        self.on_output(f"Alternative zones: {zones}")

        result = None
        last_error: Exception | None = None

        for idx, zone in enumerate(zones, start=1):
            self.merged["zone"] = zone
            self.on_output(f"⟳ Attempting zone {idx}/{len(zones)}: {zone}")

            try:
                self.stack.set_all_config(self._config_map())
                result = self.stack.up(on_output=self.on_output, parallel=4)
                self.on_output(f"✅ Provision succeeded in zone {zone}")
                return result

            except Exception as e:
                last_error = e
                self.on_output(f"⤫ Provision failed in zone {zone}: {e}")

                if is_resource_unavailable_error(e):
                    self.on_output(f"⤫ GPU/resource unavailable in {zone}. Trying next zone...")
                    continue

                self._cleanup_and_raise(
                    stack_name,
                    f"Non-retryable provisioning error: {e}",
                    e,
                )

        self._cleanup_and_raise(
            stack_name,
            f"Failed after trying all zones: {zones}. Last error: {last_error}",
            last_error,
        )

    def _validate_outputs(self, stack_name: str, result):
        outs = result.outputs or {}

        if not outs:
            self._cleanup_and_raise(
                stack_name,
                "Pulumi succeeded but returned no outputs.",
            )

        vm_name = unwrap_output(outs.get("vm_name"))
        external_ip = unwrap_output(outs.get("external_ip"))

        if not vm_name:
            self._cleanup_and_raise(
                stack_name,
                "Pulumi output missing required 'vm_name'.",
            )

        return outs, vm_name, external_ip

    def _wait_startup(self, stack_name: str, vm_name: str) -> None:
        self.on_output("Run startup-scripts ...")

        try:
            startup_ok = wait_startup_script_status(
                vm_name,
                self.merged["zone"],
                self.merged["project"],
                self.merged["maxWait"],
                10,
                10,
                on_output=self.on_output,
            )
        except Exception as e:
            self._cleanup_and_raise(
                stack_name,
                f"Startup script check failed: {e}",
                e,
            )

        if not startup_ok:
            self._cleanup_and_raise(
                stack_name,
                f"Startup script failed for VM '{vm_name}'.",
            )

    def __call__(self):
        stack_name = self.merged["stackName"]

        self._ensure_stack(stack_name)

        if self._stack_has_resources() and not self.merged.get("allow_update", False):
            raise ValueError(
                f"Stack '{stack_name}' already has running resources. "
                "Set allow_update=true to modify."
            )

        try:
            self.stack.set_all_config(self._config_map())
        except Exception as e:
            raise ValueError(f"ERROR applying stack.set_all_config() on {self.stack}: {e}") from e

        self._log_config()
        self.on_output("🚀 Launching ...")

        result = self._launch_in_available_zone(stack_name)
        outs, vm_name, external_ip = self._validate_outputs(stack_name, result)

        self._wait_startup(stack_name, vm_name)

        self.on_output(f"🏼 OUTPUT: {outs}\n")

        if external_ip:
            self.on_output(f"🚀 External IP: http://{external_ip}")

        return result