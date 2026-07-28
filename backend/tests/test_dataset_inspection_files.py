import json
import os

import pytest

from modules.dataset_inspection.raster import inspect_raster_dataset
from modules.dataset_inspection.vector import inspect_vector_dataset

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DATASET_INSPECTION") != "1",
    reason="requires Pyogrio and Rasterio integration dependencies",
)


def test_real_geojson_metadata_roundtrip(tmp_path) -> None:
    source = tmp_path / "roads.geojson"
    source.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "name": "roads",
                "crs": {
                    "type": "name",
                    "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"},
                },
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"name": "A", "lanes": 2},
                        "geometry": {
                            "type": "LineString",
                            "coordinates": [[118.7, 32.0], [118.8, 32.1]],
                        },
                    },
                    {
                        "type": "Feature",
                        "properties": {"name": "B", "lanes": 4},
                        "geometry": {
                            "type": "LineString",
                            "coordinates": [[118.8, 32.1], [118.9, 32.2]],
                        },
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    result = inspect_vector_dataset(source, original_filename="roads.geojson")

    assert result["dataset_type"] == "vector"
    assert result["format"] == "GeoJSON"
    assert result["layer_count"] == 1
    layer = result["layers"][0]
    assert layer["feature_count"] == 2
    assert layer["geometry_type"] == "LineString"
    assert layer["bounds"] == [118.7, 32.0, 118.9, 32.2]
    assert {field["name"] for field in layer["fields"]} == {"name", "lanes"}
    assert result["warnings"] == []


def test_real_geotiff_metadata_roundtrip(tmp_path) -> None:
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin

    source = tmp_path / "elevation.tif"
    data = np.array([[1, 2], [3, 4]], dtype="uint16")
    with rasterio.open(
        source,
        "w",
        driver="GTiff",
        width=2,
        height=2,
        count=1,
        dtype=data.dtype,
        crs="EPSG:4326",
        transform=from_origin(118.0, 33.0, 0.5, 0.5),
        nodata=0,
        tiled=False,
    ) as dataset:
        dataset.write(data, 1)
        dataset.set_band_description(1, "elevation")

    result = inspect_raster_dataset(source, original_filename="elevation.tif")

    assert result["dataset_type"] == "raster"
    assert result["format"] == "GeoTIFF"
    assert result["width"] == 2
    assert result["height"] == 2
    assert result["band_count"] == 1
    assert result["epsg"] == 4326
    assert result["bounds"] == [118.0, 32.0, 119.0, 33.0]
    assert result["bands"][0]["dtype"] == "uint16"
    assert result["bands"][0]["nodata"] == 0
    assert result["bands"][0]["description"] == "elevation"
    assert result["cog_readiness"]["is_tiled"] is False
    assert result["cog_readiness"]["needs_conversion"] is True
