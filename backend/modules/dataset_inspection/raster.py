import math
from pathlib import Path
from typing import Any

from modules.object_storage.keys import safe_extension

from .exceptions import DatasetInspectionError, UnsupportedDatasetFormat

_RASTER_EXTENSIONS = {".tif", ".tiff"}


def inspect_raster_dataset(path: Path, *, original_filename: str) -> dict[str, Any]:
    """Inspect GeoTIFF metadata without reading complete raster arrays."""

    extension = safe_extension(original_filename)
    if extension not in _RASTER_EXTENSIONS:
        raise UnsupportedDatasetFormat("Raster inspection currently supports GeoTIFF uploads")

    try:
        import rasterio

        with rasterio.open(path) as dataset:
            crs = dataset.crs.to_string() if dataset.crs is not None else None
            epsg = dataset.crs.to_epsg() if dataset.crs is not None else None
            bounds = [
                float(dataset.bounds.left),
                float(dataset.bounds.bottom),
                float(dataset.bounds.right),
                float(dataset.bounds.top),
            ]
            transform = [float(value) for value in dataset.transform[:6]]
            bands = []
            overview_sets: list[list[int]] = []
            for position, band_index in enumerate(dataset.indexes):
                overviews = [int(value) for value in dataset.overviews(band_index)]
                overview_sets.append(overviews)
                tags = dataset.tags(band_index)
                statistics = {
                    key.lower(): value
                    for key, value in tags.items()
                    if key.startswith("STATISTICS_")
                }
                bands.append(
                    {
                        "index": int(band_index),
                        "dtype": str(dataset.dtypes[position]),
                        "nodata": _json_number(dataset.nodatavals[position]),
                        "description": dataset.descriptions[position],
                        "unit": dataset.units[position],
                        "scale": _json_number(dataset.scales[position]),
                        "offset": _json_number(dataset.offsets[position]),
                        "color_interpretation": str(dataset.colorinterp[position].name),
                        "block_shape": [int(value) for value in dataset.block_shapes[position]],
                        "overviews": overviews,
                        "statistics": statistics,
                    }
                )

            profile = dataset.profile
            is_tiled = bool(profile.get("tiled", False))
            large_raster = max(dataset.width, dataset.height) > 512
            has_overviews = bool(overview_sets) and all(overview_sets)
            warnings: list[str] = []
            if crs is None:
                warnings.append("Raster has no coordinate reference system")
            if large_raster and not has_overviews:
                warnings.append("Large raster has no internal overviews")
            if not is_tiled:
                warnings.append("Raster is not internally tiled")

            return {
                "dataset_type": "raster",
                "format": "GeoTIFF",
                "driver": dataset.driver,
                "width": int(dataset.width),
                "height": int(dataset.height),
                "band_count": int(dataset.count),
                "crs": crs,
                "epsg": int(epsg) if epsg is not None else None,
                "bounds": bounds,
                "transform": transform,
                "bands": bands,
                "image_structure": {
                    key.lower(): value
                    for key, value in dataset.tags(ns="IMAGE_STRUCTURE").items()
                },
                "cog_readiness": {
                    "is_tiled": is_tiled,
                    "has_overviews": has_overviews,
                    "needs_conversion": not is_tiled or (large_raster and not has_overviews),
                    "compression": _optional_string(profile.get("compress")),
                },
                "warnings": warnings,
                "software": {
                    "rasterio": str(rasterio.__version__),
                    "gdal": str(rasterio.__gdal_version__),
                },
            }
    except DatasetInspectionError:
        raise
    except Exception as exc:
        raise DatasetInspectionError(f"GDAL could not open the raster dataset: {exc}") from exc


def _json_number(value: Any) -> int | float | None:
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return int(number) if number.is_integer() else number


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None
