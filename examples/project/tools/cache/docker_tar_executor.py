import textwrap

from pathlib import Path

from dagploy_dax.provisioning_lib.executor_vm import Executor
from dagploy_dax.provisioning_lib.vm_utils import extract_image_name, create_image_disks, convert_strings_to_list
from dagploy_dax.provisioning_lib.config_schema import DockerValidation
from dagploy_dax.service_lib.utils import read_required_file, prepare_proxy


class Deployment(Executor):
    """Override base _prepare() to include inference task updates."""

    def set_config(self):
        """Override base _prepare() to include inference task updates."""
        super().set_config()

        # Start customization
        cfg = self.cfg.copy()

        if cfg['service'] not in ['download_docker_image', 'download_docker']:
            raise ValueError(f"This only for download_docker service. Given {cfg['service']}")

        # validate docker settings
        dockers = DockerValidation.from_pulumi_config(self.config_dict)

        # Set extra common settings
        cfg["docker_images"] = convert_strings_to_list(self.raw_config.get("dockerImages", []))

        # get first image as first place to download docker
        cfg['first_image'] = cfg['images'][0]

        if not self.raw_config.get('family', ''):
            cfg['family'] = 'custom'

        # Update changes
        self.cfg = cfg

    def startup_builder(self):
        cfg = self.cfg

        # add proxy
        self.proxy = prepare_proxy(cfg)

        image_entries = []
        for url in cfg.get("docker_images", ""):
            if not url or not isinstance(url, str):
                continue

            image_name = extract_image_name(url)
            if not image_name:
                continue

            image_entries.append((image_name, url))

        if not image_entries:
            raise ValueError("No docker images to pull")

        extra_docker_execution = ""
        for docker_name, url in image_entries:
            extra_docker_execution += f"""
                # Remove old image inside dax
                docker exec dax bash -lc "docker images --format '{{.Repository}}:{{.Tag}}' | grep '^{docker_name}:' | xargs -r docker rmi -f"

                # Authenticate inside dax for all needed Artifact Registry hosts
                for registry in us-docker.pkg.dev asia-southeast1-docker.pkg.dev eu-docker.pkg.dev; do
                    docker exec dax bash -lc "gcloud auth print-access-token | docker login -u oauth2accesstoken --password-stdin $registry"
                done

                # Pull inside dax with retries
                for i in $(seq 1 3); do
                    echo "Pulling image inside dax: {url} (attempt $i)"
                    if docker exec dax bash -lc "docker pull {url}"; then
                        echo "✅ Successfully pulled {url} inside dax"
                        break
                    fi
                    echo "ERROR: Pull failed for {url} inside dax, retrying in $((i*5))s..."
                    sleep $((i*5))
                done
                """

        # Load the startup template
        startup_tmpl = read_required_file(Path(cfg['tools_dirpath']) / "startup_cache_docker_image.sh")

        save_docker_file = read_required_file(Path(cfg['tools_dirpath']) / "save_docker_tarfile.sh")
   
        # Add save docker tarfile script
        extra_docker_execution += "# === SAVE TAR DOCKER ===\n"
        extra_docker_execution += save_docker_file.rstrip() + "\n\n"

        startup_tmpl = startup_tmpl.replace(
            '__EXECUTION_SCRIPT__',
            f"\n# === BEGIN EXECUTION ({cfg['service']}) ===\n{extra_docker_execution}\n# === END EXECUTION ===\n"
        )

        # Save updated startup_tmpl to instance
        self.startup_tmpl = startup_tmpl

    def disk_setup(self):
        # create disk from the images immediately
        self.cache_disks, self.disk_device_names = create_image_disks(self.cfg)
        self.cfg['disk_device_names'] = self.disk_device_names

    def generate_startup_script(self):
        self.startup_script = textwrap.dedent(f"""\
#!/bin/bash
set -ex
echo "STARTUP_SCRIPT_START"

# default mount variable
MNT_BASE="/tmp"
MNT_PATHS=()

{self.proxy}

{self.variables}

sudo systemctl daemon-reload 
sudo systemctl restart docker 

# STARTUP SCRIPT
{self.startup_tmpl}

# IMPORTANT! unique monitoring flag
echo "STARTUP_SCRIPT_COMPLETE"

# Give delay for monitoring script to capture STARTUP_SCRIPT_COMPLETE
sleep 15

shutdown -h now
    """)

def deploy_cache_docker_image_program():
    """
    Pulumi deploy program that builds and provisions inference service.
    """
    # Base Pulumi config setup handled inside Executor._prepare()
    # Instantiate the Inference Executor (it runs _prepare internally)
    deployment = Deployment()
    deployment.run()