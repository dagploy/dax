# Copyright (c) 2023, DAGPLOY.  All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


MAJOR = 1
MINOR = 2
PATCH = 0
PRE_RELEASE = ""
DEV = ""

# Use the following formatting: (major, minor, patch, pre-release)
VERSION = (MAJOR, MINOR, PATCH, PRE_RELEASE, DEV)

__shortversion__ = ".".join(map(str, VERSION[:3]))
__version__ = __shortversion__

if VERSION[3] != "":
    __version__ = __version__ + VERSION[3]

if VERSION[4] != "":
    __version__ = __version__ + "." + ".".join(VERSION[4:])

import os as _os  # noqa: E402, I001
import subprocess as _subprocess  # noqa: E402


if not int(_os.getenv("NO_VCS_VERSION", "0")):
    try:
        _git = _subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607
            capture_output=True,
            cwd=_os.path.dirname(_os.path.abspath(__file__)),
            check=True,
            text=True,
        )
    except (_subprocess.CalledProcessError, OSError):
        pass
    else:
        __version__ += f"+{_git.stdout.strip()}"

__package_name__ = "dagploy_dax"
__contact_names__ = "DAGPLOY"
__contact_emails__ = "support@dagploy.com"
__homepage__ = "https://www.dagploy.com/dax/docs/index.html"
__repository_url__ = "https://github.com/dagploy/dax"
__download_url__ = "https://github.com/dagploy/dax/releases"
__description__ = "Dagploy DAX - Automated Infra provisioning for running LLM workloads on Cloud and On-Premises"
__license__ = "Apache2"
__keywords__ = "AI infra, AIOps, AI Devops, GCP, AI pipelines, Local AI, Infrastructure as Code, Deploy AI "