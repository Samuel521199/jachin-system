"""由 smoke-publish-desktop-release.ps1 调用：读环境变量，上传 MinIO，POST admin/desktop-releases。"""
from __future__ import annotations

import json
import os
import sys
from urllib import error, request

from botocore.client import Config

try:
    import boto3
except ImportError:
    print("请先执行: pip install boto3", file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    sig_path = os.environ["_SIG_PATH"]
    tmp_dummy = os.environ["_TMP_DUMMY"]
    object_key = os.environ["_OBJECT_KEY"]
    version = os.environ["_VERSION"]
    pub_date = os.environ["_PUB_DATE"]

    import base64

    sig = base64.standard_b64encode(open(sig_path, "rb").read()).decode("ascii")

    force = (os.environ.get("DESKTOP_RELEASES_S3_FORCE_PATH_STYLE") or "true").lower() in (
        "1",
        "true",
        "yes",
    )
    client = boto3.client(
        "s3",
        endpoint_url=os.environ["DESKTOP_RELEASES_S3_ENDPOINT"],
        region_name=os.environ.get("DESKTOP_RELEASES_S3_REGION") or "us-east-1",
        aws_access_key_id=os.environ["DESKTOP_RELEASES_S3_ACCESS_KEY"],
        aws_secret_access_key=os.environ["DESKTOP_RELEASES_S3_SECRET_KEY"],
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path" if force else "auto"},
        ),
    )
    bucket = os.environ["DESKTOP_RELEASES_S3_BUCKET"]
    print(f"Uploading -> s3://{bucket}/{object_key}")
    client.upload_file(tmp_dummy, bucket, object_key)

    body = json.dumps(
        {
            "version": version,
            "pub_date": pub_date,
            "notes": "smoke test (ps1)",
            "artifacts": {
                "windows-x86_64": {"objectKey": object_key, "signature": sig},
            },
        },
        ensure_ascii=False,
    ).encode("utf-8")

    base = os.environ["NEXUS_BASE_URL"].rstrip("/")
    url = base + "/api/v1/admin/desktop-releases"
    req = request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "X-Admin-Token": os.environ["NEXUS_ADMIN_SECRET"],
        },
    )
    try:
        with request.urlopen(req, timeout=120) as resp:
            print(resp.status, resp.read().decode("utf-8", errors="replace"))
    except error.HTTPError as e:
        print(e.code, e.read().decode("utf-8", errors="replace"), file=sys.stderr)
        raise SystemExit(1) from e


if __name__ == "__main__":
    main()
