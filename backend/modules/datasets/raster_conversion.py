import math
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from django.conf import settings
from rasterio.enums import Resampling
from rasterio.warp import transform_bounds


class RasterConversionError(RuntimeError):
    """Raised when a source raster cannot be converted into a publishable COG."""


@dataclass(frozen=True, slots=True)
class RasterCogMetadata:
    width: int
    height: int
    band_count: int
    crs: str
    epsg: int | None
    bounds: list[float]
    transform: list[float]
    bands: list[dict[str, Any]]
    statistics: list[dict[str, Any]]
    image_structure: dict[str, Any]
    cog_profile: dict[str, Any]
    min_zoom: int
    max_zoom: int
    size: int


def convert_to_cog(*, source: Path, destination: Path) -> RasterCogMetadata:
    """Create a canonical tiled COG and return bounded publication metadata."""

    executable = str(getattr(settings, "GDAL_TRANSLATE_EXECUTABLE", "gdal_translate"))
    if shutil.which(executable) is None:
        raise RasterConversionError(f"Required executable is unavailable: {executable}")
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.unlink(missing_ok=True)

    command = [
        executable,
        str(source),
        str(destination),
        "-of",
        "COG",
        "-co",
        "COMPRESS=DEFLATE",
        "-co",
        "PREDICTOR=YES",
        "-co",
        "BLOCKSIZE=512",
        "-co",
        "BIGTIFF=IF_SAFER",
        "-co",
        "NUM_THREADS=ALL_CPUS",
        "-co",
        "OVERVIEWS=IGNORE_EXISTING",
    ]
    timeout = int(getattr(settings, "RASTER_COG_TIMEOUT", 60 * 60))
    completed = subprocess.run(
        command,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
        env={**os.environ, "CPL_TMPDIR": str(destination.parent)},
    )
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "gdal_translate failed").strip()
        raise RasterConversionError(message[-4000:])
    if not destination.is_file():
        raise RasterConversionError("COG conversion did not produce an output file")

    size = destination.stat().st_size
    maximum = int(getattr(settings, "RASTER_COG_MAX_BYTES", 50 * 1024**3))
    if size <= 0:
        raise RasterConversionError("COG conversion produced an empty file")
    if size > maximum:
        raise RasterConversionError(
            f"COG output exceeded the configured {maximum}-byte limit"
        )
    return inspect_cog(destination)


def inspect_cog(path: Path) -> RasterCogMetadata:
    """Validate a generated COG and compute bounded band statistics."""

    try:
        with rasterio.open(path) as dataset:
            if dataset.crs is None:
                raise RasterConversionError("Raster must define a coordinate reference system")
            if dataset.driver != "GTiff":
                raise RasterConversionError("Published raster must be a GeoTIFF")
            if not dataset.is_tiled:
                raise RasterConversionError("Published raster must use internal tiling")

            structure = dataset.tags(ns="IMAGE_STRUCTURE")
            layout = str(structure.get("LAYOUT", ""))
            if layout and layout.upper() != "COG":
                raise RasterConversionError("Generated GeoTIFF is not marked as COG layout")

            wgs84_bounds = transform_bounds(
                dataset.crs,
                "EPSG:4326",
                *dataset.bounds,
                densify_pts=21,
            )
            bounds = [float(value) for value in wgs84_bounds]
            statistics = _band_statistics(dataset)
            bands = [
                {
                    "index": index,
                    "dtype": dataset.dtypes[index - 1],
                    "nodata": dataset.nodatavals[index - 1],
                    "description": dataset.descriptions[index - 1],
                    "unit": dataset.units[index - 1],
                    "scale": dataset.scales[index - 1],
                    "offset": dataset.offsets[index - 1],
                    "color_interpretation": dataset.colorinterp[index - 1].name,
                    "overviews": dataset.overviews(index),
                    "block_shape": list(dataset.block_shapes[index - 1]),
                }
                for index in dataset.indexes
            ]
            min_zoom, max_zoom = _web_mercator_zoom_range(dataset)
            profile = {
                "driver": dataset.driver,
                "is_tiled": dataset.is_tiled,
                "layout": layout or "COG",
                "compression": structure.get("COMPRESSION"),
                "interleave": structure.get("INTERLEAVE"),
                "block_shapes": [list(shape) for shape in dataset.block_shapes],
                "overview_levels": {
                    str(index): dataset.overviews(index) for index in dataset.indexes
                },
                "validated": True,
            }
            return RasterCogMetadata(
                width=dataset.width,
                height=dataset.height,
                band_count=dataset.count,
                crs=dataset.crs.to_string(),
                epsg=dataset.crs.to_epsg(),
                bounds=bounds,
                transform=list(dataset.transform)[:6],
                bands=bands,
                statistics=statistics,
                image_structure={str(key): value for key, value in structure.items()},
                cog_profile=profile,
                min_zoom=min_zoom,
                max_zoom=max_zoom,
                size=Path(path).stat().st_size,
            )
    except RasterConversionError:
        raise
    except (OSError, ValueError, rasterio.errors.RasterioError) as exc:
        raise RasterConversionError(f"Could not validate generated COG: {exc}") from exc


def _band_statistics(dataset) -> list[dict[str, Any]]:
    maximum = max(int(getattr(settings, "RASTER_STATISTICS_MAX_SIZE", 1024)), 32)
    scale = max(dataset.width / maximum, dataset.height / maximum, 1.0)
    out_width = max(1, int(round(dataset.width / scale)))
    out_height = max(1, int(round(dataset.height / scale)))
    result: list[dict[str, Any]] = []
    for index in dataset.indexes:
        array = dataset.read(
            index,
            out_shape=(out_height, out_width),
            masked=True,
            resampling=Resampling.nearest,
        )
        values = np.asarray(array.compressed(), dtype=np.float64)
        total = int(array.size)
        valid = int(values.size)
        if valid == 0:
            result.append(
                {
                    "band": index,
                    "sample_width": out_width,
                    "sample_height": out_height,
                    "valid_count": 0,
                    "total_count": total,
                    "valid_percent": 0.0,
                    "minimum": None,
                    "maximum": None,
                    "mean": None,
                    "standard_deviation": None,
                    "percentile_2": None,
                    "percentile_98": None,
                    "histogram": {"counts": [], "edges": []},
                }
            )
            continue
        minimum = float(np.min(values))
        maximum_value = float(np.max(values))
        percentile_2, percentile_98 = np.percentile(values, [2, 98])
        histogram_counts, histogram_edges = np.histogram(values, bins=20)
        result.append(
            {
                "band": index,
                "sample_width": out_width,
                "sample_height": out_height,
                "valid_count": valid,
                "total_count": total,
                "valid_percent": round(valid / total * 100.0, 6),
                "minimum": minimum,
                "maximum": maximum_value,
                "mean": float(np.mean(values)),
                "standard_deviation": float(np.std(values)),
                "percentile_2": float(percentile_2),
                "percentile_98": float(percentile_98),
                "histogram": {
                    "counts": [int(value) for value in histogram_counts],
                    "edges": [float(value) for value in histogram_edges],
                },
            }
        )
    return result


def _web_mercator_zoom_range(dataset) -> tuple[int, int]:
    try:
        west, south, east, north = transform_bounds(
            dataset.crs,
            "EPSG:3857",
            *dataset.bounds,
            densify_pts=21,
        )
        resolution = max(
            abs(east - west) / max(dataset.width, 1),
            abs(north - south) / max(dataset.height, 1),
        )
        if not math.isfinite(resolution) or resolution <= 0:
            raise ValueError("invalid resolution")
        initial_resolution = 2 * math.pi * 6_378_137 / 256
        maximum_zoom = int(math.floor(math.log2(initial_resolution / resolution)))
    except (OverflowError, ValueError):
        maximum_zoom = int(getattr(settings, "RASTER_TILE_MAX_ZOOM", 22))

    configured_min = int(getattr(settings, "RASTER_TILE_MIN_ZOOM", 0))
    configured_max = int(getattr(settings, "RASTER_TILE_MAX_ZOOM", 22))
    maximum_zoom = min(max(maximum_zoom, configured_min), configured_max)
    pyramid_levels = max(
        0,
        int(math.ceil(math.log2(max(dataset.width, dataset.height) / 256))),
    )
    minimum_zoom = max(configured_min, maximum_zoom - pyramid_levels)
    return minimum_zoom, maximum_zoom
