import os
import shutil
from pathlib import Path

import pytest

if os.environ.get("RUN_RASTER_COG_INTEGRATION") != "1":
    pytest.skip("real COG conversion integration is disabled", allow_module_level=True)

import numpy as np
import rasterio
from rasterio.transform import from_origin

from modules.datasets.raster_conversion import convert_to_cog


def test_real_geotiff_is_converted_to_valid_cog(tmp_path: Path) -> None:
    if shutil.which("gdal_translate") is None:
        pytest.skip("gdal_translate is unavailable")

    source = tmp_path / "source.tif"
    destination = tmp_path / "published.cog.tif"
    height = 1024
    width = 1024
    values = np.arange(height * width, dtype=np.uint16).reshape(height, width)
    with rasterio.open(
        source,
        "w",
        driver="GTiff",
        width=width,
        height=height,
        count=1,
        dtype="uint16",
        crs="EPSG:4326",
        transform=from_origin(118.0, 32.0, 0.001, 0.001),
        tiled=False,
        nodata=0,
    ) as dataset:
        dataset.write(values, 1)

    metadata = convert_to_cog(source=source, destination=destination)

    assert destination.is_file()
    assert metadata.width == width
    assert metadata.height == height
    assert metadata.band_count == 1
    assert metadata.epsg == 4326
    assert metadata.cog_profile["validated"] is True
    assert metadata.cog_profile["layout"] == "COG"
    assert metadata.bands[0]["block_shape"] == [512, 512]
    assert metadata.bands[0]["overviews"]
    assert metadata.statistics[0]["valid_count"] > 0
    assert metadata.statistics[0]["maximum"] > metadata.statistics[0]["minimum"]
    assert metadata.min_zoom <= metadata.max_zoom
