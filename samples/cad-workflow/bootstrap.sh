#!/bin/bash
set -euo pipefail
exec > >(tee /var/log/cad-workflow-bootstrap.log) 2>&1
export DEBIAN_FRONTEND=noninteractive
sed -i 's|http://|https://|g' /etc/apt/sources.list.d/ubuntu.sources
apt-get update
apt-get install -y --no-install-recommends \
  ca-certificates curl python3-venv xvfb xauth fontconfig fonts-dejavu-core \
  libgl1 libegl1 libglu1-mesa libgl1-mesa-dri libglx-mesa0 dbus-x11 \
  libxkbcommon-x11-0 libxcb-cursor0 \
  libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 \
  libxcb-xinerama0 libxcb-randr0 libxcb-shape0 libxcb-xfixes0 \
  libxrender1 libxext6 libxi6 libsm6 libice6 libnss3 libasound2t64
python3 -m venv /opt/cad-control
/opt/cad-control/bin/pip install --disable-pip-version-check \
  boto3==1.43.106 botocore==1.43.114 s3transfer==0.19.2 urllib3==2.8.0
useradd --system --create-home --shell /usr/sbin/nologin cadauthor
useradd --system --create-home --shell /usr/sbin/nologin cadcheck
mkdir -p /opt/cad-workflow /var/lib/cad-workflow/jobs
chmod 755 /opt/cad-workflow /var/lib/cad-workflow /var/lib/cad-workflow/jobs
python3 - <<'PY'
import base64, gzip, json
from pathlib import Path
files = json.loads(gzip.decompress(base64.b64decode("@@SOURCE_BUNDLE@@")))
root = Path("/opt/cad-workflow")
for name, text in files.items():
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o644)
PY
cat >/etc/cad-workflow.env <<'ENV'
export CAD_BUCKET='${Artifacts}'
export CAD_BACKEND='freecad'
export AWS_DEFAULT_REGION='${AWS::Region}'
export AWS_REGION='${AWS::Region}'
ENV
chmod 600 /etc/cad-workflow.env
curl --fail --location --retry 3 --connect-timeout 10 --max-time 600 \
  -o /tmp/FreeCAD.AppImage \
  'https://github.com/FreeCAD/FreeCAD/releases/download/1.1.4/FreeCAD_1.1.4-Linux-x86_64-py311.AppImage'
echo 'f6dc6ba676e5ac96a565ebc8d657232f94c6158e85b4352141bd1a46f6b43434  /tmp/FreeCAD.AppImage' | sha256sum -c -
sha256sum /tmp/FreeCAD.AppImage >/opt/cad-workflow/freecad-release.sha256
chmod 755 /tmp/FreeCAD.AppImage
cd /opt
/tmp/FreeCAD.AppImage --appimage-extract >/dev/null
mv /opt/squashfs-root /opt/freecad
rm /tmp/FreeCAD.AppImage
chmod -R a+rX /opt/freecad
/opt/cad-control/bin/pip freeze >/opt/cad-workflow/control-requirements.txt
. /etc/cad-workflow.env
/opt/cad-control/bin/python - <<'PY'
import json, os, platform, time
import boto3
from botocore.config import Config
record = {
    "status": "bootstrap_complete",
    "time": time.time(),
    "architecture": platform.machine(),
    "freecad_release": "1.1.4",
    "freecad_sha256": "f6dc6ba676e5ac96a565ebc8d657232f94c6158e85b4352141bd1a46f6b43434",
    "source_sha256": "@@SOURCE_SHA@@",
    "native_task_verified": False,
}
session = boto3.Session(region_name=os.environ["AWS_REGION"])
config = Config(connect_timeout=5, read_timeout=20,
                retries={"total_max_attempts": 3, "mode": "standard"})
session.client("s3", config=config).put_object(
    Bucket=os.environ["CAD_BUCKET"], Key="health/bootstrap.json",
    Body=json.dumps(record).encode(), ServerSideEncryption="AES256",
)
session.client("cloudformation", config=config).signal_resource(
    StackName="${AWS::StackName}", LogicalResourceId="Worker",
    UniqueId="bootstrap", Status="SUCCESS",
)
print(json.dumps(record))
PY
