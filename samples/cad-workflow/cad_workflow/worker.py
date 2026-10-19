"""Root-owned SSM entry point; CAD author and checker run without cloud access."""

import argparse
import base64
import fcntl
import json
import os
from pathlib import Path
import pwd
import stat
import subprocess
import time
import traceback

import boto3
from botocore.config import Config

from cad_workflow.backends import get_backend
from cad_workflow.contract import (
    MAX_FILE_BYTES, SCHEMA, digest, json_bytes, request, require_verified_report,
)

ROOT = Path("/var/lib/cad-workflow")
CODE = Path("/opt/cad-workflow")


def event(kind, **values):
    print(json.dumps({"event": kind, "time": time.time(), **values}, allow_nan=False), flush=True)


def read_regular(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 0 < info.st_size <= MAX_FILE_BYTES:
            raise ValueError("Artifact must be a bounded regular file with exactly one link")
        value = handle.read(MAX_FILE_BYTES + 1)
        after = os.fstat(handle.fileno())
        if len(value) != info.st_size or (info.st_size, info.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError("Artifact changed while it was read")
        return value


def write_new(path, value, mode=0o444):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())


def phase(identifier, name, directory, request_path, source=None):
    backend = get_backend(os.environ.get("CAD_BACKEND", "freecad"))
    user = "cadauthor" if name == "author" else "cadcheck"
    identity = pwd.getpwnam(user)
    directory.mkdir(mode=0o700)
    os.chown(directory, identity.pw_uid, identity.pw_gid)
    environment = {
        "CAD_REQUEST": str(request_path),
        "CAD_OUTPUT": str(directory),
        "CAD_SOURCE": str(source or directory),
        "QT_QPA_PLATFORM": "xcb",
        "LIBGL_ALWAYS_SOFTWARE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "HOME": str(directory),
        "XDG_DATA_HOME": str(directory / "data"),
        "XDG_CONFIG_HOME": str(directory / "config"),
        "XDG_CACHE_HOME": str(directory / "cache"),
        "XDG_RUNTIME_DIR": str(directory / "runtime"),
        "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
    }
    unit = f"cad-{identifier}-{name}"
    properties = {
        "User": user, "Group": user, "UMask": "0077",
        "WorkingDirectory": str(directory), "ReadWritePaths": str(directory),
        "PrivateNetwork": "yes", "PrivateTmp": "yes", "PrivateDevices": "yes",
        "NoNewPrivileges": "yes", "ProtectSystem": "strict", "ProtectHome": "yes",
        "ProtectKernelTunables": "yes", "ProtectKernelModules": "yes",
        "ProtectControlGroups": "yes", "RestrictSUIDSGID": "yes",
        "MemoryMax": "2G", "TasksMax": "128", "RuntimeMaxSec": "90",
        "KillMode": "control-group", "TimeoutStopSec": "5",
    }
    command = ["systemd-run", "--quiet", "--wait", "--pipe", "--collect", "--unit", unit]
    for key, value in properties.items():
        command.extend(["--property", f"{key}={value}"])
    for key, value in environment.items():
        command.extend(["--setenv", f"{key}={value}"])
    command.extend(backend.command(name, directory, CODE))
    started = time.monotonic()
    event("phase_started", job_id=identifier, phase=name, user=user, private_network=True)
    try:
        with (directory.parent / f"{name}.log").open("wb") as output:
            result = subprocess.run(command, stdout=output, stderr=subprocess.STDOUT, timeout=105, check=False)
    except subprocess.TimeoutExpired:
        subprocess.run(["systemctl", "stop", unit], timeout=15, check=True, capture_output=True)
        raise
    event("phase_completed", job_id=identifier, phase=name, exit_code=result.returncode,
          elapsed_seconds=round(time.monotonic() - started, 3))
    return result.returncode


def publish(client, bucket, parameters, result, files):
    records = {}
    for name, path in files.items():
        data = read_regular(path)
        key = f"jobs/{parameters['job_id']}/{name}"
        response = client.put_object(
            Bucket=bucket, Key=key, Body=data, IfNoneMatch="*",
            ServerSideEncryption="AES256", Metadata={"sha256": digest(data)},
        )
        records[name] = {"key": key, "sha256": digest(data), "bytes": len(data),
                         "version_id": response.get("VersionId")}
    result["artifacts"] = records
    result["bucket"] = bucket
    client.put_object(Bucket=bucket, Key=f"jobs/{parameters['job_id']}/result.json",
                      Body=json_bytes(result), ContentType="application/json",
                      IfNoneMatch="*", ServerSideEncryption="AES256")
    return result


def execute(parameters):
    backend = get_backend(os.environ.get("CAD_BACKEND", "freecad"))
    session = boto3.Session(region_name=os.environ.get("AWS_REGION"))
    client = session.client("s3", config=Config(
        connect_timeout=5, read_timeout=20, retries={"total_max_attempts": 2, "mode": "standard"}))
    bucket = os.environ["CAD_BUCKET"]
    identifier = parameters["job_id"]
    ROOT.mkdir(exist_ok=True, mode=0o755)
    (ROOT / "jobs").mkdir(exist_ok=True, mode=0o755)
    started = time.monotonic()
    with (ROOT / "worker.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        directory = ROOT / "jobs" / identifier
        if directory.exists():
            if read_regular(directory / "request.json") != json_bytes(parameters):
                raise ValueError("Job ID already belongs to different parameters")
            if (directory / "result.json").exists():
                return json.loads(read_regular(directory / "result.json"))
            raise RuntimeError("Prior job is incomplete; preserve it and use a new job ID")
        directory.mkdir(mode=0o755)
        request_path = directory / "request.json"
        write_new(request_path, json_bytes(parameters))
        result = {"schema": SCHEMA, "backend": backend.NAME,
                  "job_id": identifier, "status": "failed",
                  "accepted": False, "parameters": parameters,
                  "request_sha256": digest(json_bytes(parameters)),
                  "code_sha256": {name: digest((CODE / "cad_workflow" / name).read_bytes())
                                  for name in ("contract.py", "backends/__init__.py", "worker.py",
                                               *backend.SOURCE_FILES)}}
        files = {}
        try:
            if phase(identifier, "author", directory / "author", request_path) != 0:
                raise RuntimeError("Native author failed")
            frozen = directory / "frozen"
            frozen.mkdir(mode=0o755)
            for name in backend.CANDIDATE_FILES:
                write_new(frozen / name, read_regular(directory / "author" / name))
                files[name] = frozen / name
            os.chmod(frozen, 0o555)
            event("candidate_frozen", job_id=identifier,
                  sha256={name: digest(read_regular(path)) for name, path in files.items()})
            code = phase(identifier, "verifier", directory / "verifier", request_path, frozen)
            checks = json.loads(read_regular(directory / "verifier" / "checks.json"))
            write_new(directory / "checks.json", json_bytes(checks))
            files["checks.json"] = directory / "checks.json"
            if code != 0:
                raise RuntimeError("Native verifier rejected the candidate")
            require_verified_report(
                checks, backend.NAME,
                {name: digest(read_regular(frozen / name)) for name in backend.CHECKED_FILES},
            )
            result.update(status="accepted", accepted=True, checks=checks)
        except Exception as error:
            result["error"] = str(error)
            write_new(directory / "failure.txt", traceback.format_exc().encode())
            files["failure.txt"] = directory / "failure.txt"
        for name in ("author.log", "verifier.log"):
            if (directory / name).exists() and (directory / name).stat().st_size:
                files[name] = directory / name
        for name in ("author", "verifier"):
            diagnostic = directory / name / f"{name}-error.txt"
            if diagnostic.exists():
                files[diagnostic.name] = diagnostic
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        publish(client, bucket, parameters, result, files)
        write_new(directory / "result.json", json_bytes(result))
        event("job_completed", job_id=identifier, status=result["status"],
              elapsed_seconds=result["elapsed_seconds"])
        return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request-base64", required=True)
    args = parser.parse_args()
    if len(args.request_base64) > 4096:
        raise ValueError("Request exceeds the local bound")
    value = json.loads(base64.b64decode(args.request_base64, validate=True))
    result = execute(request(value))
    print(json.dumps(result, allow_nan=False))
    return 0 if result["accepted"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
