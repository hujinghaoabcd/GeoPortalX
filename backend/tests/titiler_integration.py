import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

import boto3
import numpy as np
import rasterio
from botocore.config import Config
from rasterio.transform import from_origin

BUCKET = os.environ.get("TITILER_TEST_BUCKET", "geoportalx-titiler")
KEY = "rasters/integration/test.cog.tif"
ENDPOINT = os.environ.get("TITILER_TEST_S3_ENDPOINT", "http://127.0.0.1:9000")
TITILER = os.environ.get("TITILER_TEST_URL", "http://127.0.0.1:8001").rstrip("/")
ACCESS_KEY = os.environ.get("S3_ACCESS_KEY", "geoportalx")
SECRET_KEY = os.environ.get("S3_SECRET_KEY", "change-me-now")


def _client():
    return boto3.client(
        "s3",
        endpoint_url=ENDPOINT,
        aws_access_key_id=ACCESS_KEY,
        aws_secret_access_key=SECRET_KEY,
        region_name="us-east-1",
        config=Config(s3={"addressing_style": "path"}),
    )


def setup() -> None:
    workspace = Path("/tmp/geoportalx-titiler-integration")
    workspace.mkdir(parents=True, exist_ok=True)
    source = workspace / "source.tif"
    cog = workspace / "source.cog.tif"
    values = np.arange(1024 * 1024, dtype=np.uint16).reshape(1024, 1024)
    with rasterio.open(
        source,
        "w",
        driver="GTiff",
        width=1024,
        height=1024,
        count=1,
        dtype="uint16",
        crs="EPSG:4326",
        transform=from_origin(118.0, 32.0, 0.001, 0.001),
        nodata=0,
    ) as dataset:
        dataset.write(values, 1)
    subprocess.run(
        [
            "gdal_translate",
            str(source),
            str(cog),
            "-of",
            "COG",
            "-co",
            "COMPRESS=DEFLATE",
            "-co",
            "BLOCKSIZE=512",
            "-co",
            "OVERVIEWS=IGNORE_EXISTING",
        ],
        check=True,
    )
    client = _client()
    try:
        client.create_bucket(Bucket=BUCKET)
    except client.exceptions.BucketAlreadyOwnedByYou:
        pass
    client.upload_file(
        str(cog),
        BUCKET,
        KEY,
        ExtraArgs={"ContentType": "image/tiff"},
    )
    print(json.dumps({"bucket": BUCKET, "key": KEY, "size": cog.stat().st_size}))


def _read(path: str, parameters: dict[str, object] | None = None) -> tuple[bytes, str]:
    query = f"?{urlencode(parameters or {}, doseq=True)}" if parameters else ""
    with urlopen(f"{TITILER}{path}{query}", timeout=20) as response:
        return response.read(), response.headers.get("Content-Type", "")


def check() -> None:
    for _ in range(60):
        try:
            _read("/openapi.json")
            break
        except OSError:
            time.sleep(1)
    else:
        raise RuntimeError("TiTiler did not become ready")

    asset = f"s3://{BUCKET}/{KEY}"
    info_body, _ = _read("/cog/info", {"url": asset})
    info = json.loads(info_body)
    assert info["width"] == 1024
    assert info["height"] == 1024
    assert len(info["band_metadata"]) == 1

    tile_body, tile_type = _read(
        "/cog/tiles/WebMercatorQuad/8/212/104.png",
        {
            "url": asset,
            "bidx": 1,
            "rescale": "0,65535",
            "colormap_name": "viridis",
        },
    )
    assert tile_body.startswith(b"\x89PNG\r\n\x1a\n")
    assert "image/png" in tile_type

    point_body, _ = _read("/cog/point/118.5,31.5", {"url": asset})
    point = json.loads(point_body)
    assert "values" in point
    print(json.dumps({"info": info, "point": point}, default=str))


if __name__ == "__main__":
    action = sys.argv[1]
    if action == "setup":
        setup()
    elif action == "check":
        check()
    else:
        raise SystemExit(f"Unknown action: {action}")
