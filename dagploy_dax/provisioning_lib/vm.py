import pulumi
from pulumi_gcp import compute
from dagploy_dax.provisioning_lib.vm_utils import create_disk, provision_gcp_vm, make_wait_command, setup_firewall
from pulumi import ResourceOptions
import os


class VMProvisioning:
    def __init__(self, cfg, disk_executions, disk_device_names, cache_disks):
        self.cfg = cfg
        self.disk_executions = disk_executions
        self.disk_device_names = disk_device_names
        self.open_public_access = cfg.get("public_mode", False)
        self.depends_on = []
        self.cache_disks = cache_disks
        self.compute_instance = None
        self.disk_already_attached = False

    def execute(self):
        self._apply_network_tags()
        self._provision_vm_and_disks()
        self._attach_cache_disks()
        self._setup_firewall()
        self._export_network_outputs()
        self._export_metadata()

        return self.compute_instance, self.cache_disks, self.depends_on

    def _apply_network_tags(self):
        if not self.open_public_access:
            self.cfg["tags"] = self.cfg["tags"] + ["nat"]

    def _provision_vm_and_disks(self):
        pulumi.log.info(f"DEBUG: ...")

        pulumi.log.info(f"CONFIGURATION PROJECT : {self.cfg['project']}")
        pulumi.log.info(f"CONFIGURATION SERVICE ACCOUNT : {self.cfg['service_account']}")
        pulumi.log.info(f"CONFIGURATION NETWORK : {self.cfg['network']}")
        pulumi.log.info(f"CONFIGURATION SUBNETWORK : {self.cfg['subnetwork']}")

        pulumi.log.info(f"PULUMI ENV GOOGLE_APPLICATION_CREDENTIALS:  {os.environ.get('GOOGLE_APPLICATION_CREDENTIALS', '')}")
        pulumi.log.info(f"PULUMI ENV PULUMI_CONFIG_PASSPHRASE:  {os.environ.get('PULUMI_CONFIG_PASSPHRASE', '')}")
        pulumi.log.info(f"PULUMI ENV GOOGLE_CLOUD_PROJECT:  {os.environ.get('GOOGLE_CLOUD_PROJECT', '')}")

        if "gpu" not in self.cfg:
            if not self.cache_disks:
                self.cache_disks = create_disk(self.disk_executions)
                pulumi.log.info("🏱 NO GPU required. Create disk in parallel")
                self.compute_instance = provision_gcp_vm(self.cfg)
            else:
                # FIX ISSUE: expected non-nil error with nil state during Create of
                # Where disk is created early and get attached while VM status is not ready
                # Pulumi depends on not checking whether the VM status is running, therefore attachment is too early.
                attached_disks_args = [
                    compute.InstanceAttachedDiskArgs(
                        source=disk.self_link,
                        device_name=self.disk_device_names[idx],
                        mode="READ_WRITE",
                    )
                    for idx, disk in enumerate(self.cache_disks)
                ]
                pulumi.log.info("🏱 Empty disk creation mode. Attach in compute arguments")

                # give indicator to not attach again
                self.disk_already_attached = True

                self.compute_instance = provision_gcp_vm(self.cfg, [], attached_disks_args)

            return

        pulumi.log.info("🌀 GPU required. Disk waiting for VM initialization ..")

        self.compute_instance = provision_gcp_vm(self.cfg)
        wait_for_vm = make_wait_command(self.cfg, depends_on=[self.compute_instance])
        self.cache_disks = create_disk(self.disk_executions, depends_on=[wait_for_vm])
        self.depends_on = [wait_for_vm]

    def _attach_cache_disks(self):
        if not self.disk_already_attached:
            for idx, disk in enumerate(self.cache_disks):
                compute.AttachedDisk(
                    f"cache-disk-{idx}",
                    instance=self.compute_instance.name,
                    disk=disk.id,
                    device_name=self.disk_device_names[idx],
                    zone=self.cfg["zone"],
                    opts=ResourceOptions(
                        depends_on=[self.compute_instance, disk]
                    ),
                )

    def _setup_firewall(self):
        setup_firewall(self.cfg, self.depends_on)

    def _export_network_outputs(self):
        port = str(self.cfg["port"]) if "port" in self.cfg else ""

        if self.open_public_access:
            external_ip = self.compute_instance.network_interfaces.apply(
                lambda nics: nics[0]["access_configs"][0]["nat_ip"]
            )
            url = pulumi.Output.concat("http://", external_ip, ":", port)
            pulumi.export("external_ip", external_ip)
            pulumi.export("url", url)
            return

        private_ip = self.compute_instance.network_interfaces.apply(
            lambda nics: nics[0]["network_ip"]
        )

        url = pulumi.Output.concat("http://", private_ip, ":", port)
        pulumi.export("private_ip", private_ip)
        pulumi.export("url", url)

    def _export_metadata(self):
        pulumi.export("vm_name", self.compute_instance.name)
        pulumi.export("zone", self.compute_instance.zone)
        for idx, disk in enumerate(self.cache_disks):
            pulumi.export(f"cache_disk_{idx}", disk.name)
        pulumi.export("stack_name", self.cfg["stack_name"])

