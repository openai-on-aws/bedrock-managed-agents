#!/usr/bin/env python3
"""Install a pinned, checksum-verified official cfn-guard into this template only."""

import hashlib
import io
from pathlib import Path
import platform
import tarfile
import urllib.request

RELEASE = "3.2.1"
ASSETS = {
    ("Darwin", "arm64"): (
        "cfn-guard-v3-aarch64-macos-latest.tar.gz",
        "4c1eb10c061731159eaaf0e7dbd465db9fa4b767b82186a4ab489671cc00b7d0"),
    ("Darwin", "x86_64"): (
        "cfn-guard-v3-x86_64-macos-latest.tar.gz",
        "5089dfaa05a766cf118a020518e62f77eddbd43acf3a0b69d36b23175c6c6fda"),
    ("Linux", "aarch64"): (
        "cfn-guard-v3-aarch64-linux-latest.tar.gz",
        "cd378026dad0f865926ab1d1c082e2faf825f7fd888a9fe6b5c142cdf175c129"),
    ("Linux", "x86_64"): (
        "cfn-guard-v3-x86_64-linux-latest.tar.gz",
        "8c66efb19c63e6c2bf26b9a41bbcf2f85baa8a937b01d350940194faaf64cf1d"),
}


def main():
    name, expected = ASSETS[(platform.system(), platform.machine())]
    url = f"https://github.com/aws-cloudformation/cloudformation-guard/releases/download/{RELEASE}/{name}"
    with urllib.request.urlopen(url, timeout=60) as response:
        data = response.read(8 * 1024 * 1024)
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError("Official cfn-guard archive checksum does not match the pinned release")
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        members = [member for member in archive.getmembers()
                   if member.isfile() and Path(member.name).name == "cfn-guard"]
        if len(members) != 1:
            raise ValueError("Release archive does not contain exactly one cfn-guard binary")
        content = archive.extractfile(members[0]).read()
    destination = Path(__file__).resolve().parent / ".tools" / "cfn-guard"
    destination.parent.mkdir(exist_ok=True)
    destination.write_bytes(content)
    destination.chmod(0o755)
    print(f"Installed official cfn-guard {RELEASE}: {destination}")


if __name__ == "__main__":
    main()
