from collections.abc import Callable
from pathlib import Path
from typing import Any

from modules.object_storage.keys import safe_extension

from .archive import inspect_shapefile_archive
from .exceptions import DatasetInspectionError, UnsupportedDatasetFormat

_VECTOR_EXTENSIONS = {".geojson", ".json", ".gpkg", ".zip"}
ProgressCallback = Callable[[int, int, str], None]


def inspect_vector_dataset(
    path: Path,
    *,
    original_filename: str,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Inspect vector structure and metadata without loading all features into memory."""

    extension = safe_extension(original_filename)
    if extension not in _VECTOR_EXTENSIONS:
        raise UnsupportedDatasetFormat(
            "Vector inspection supports GeoJSON, GeoPackage and Shapefile ZIP uploads"
        )

    archive_result = None
    source: str | Path = path
    warnings: list[str] = []
    if extension == ".zip":
        archive_result = inspect_shapefile_archive(path)
        warnings.extend(archive_result.warnings)
        source = f"/vsizip/{path.as_posix()}"

    try:
        import pyogrio

        raw_layers = pyogrio.list_layers(source)
    except Exception as exc:
        raise DatasetInspectionError(f"GDAL could not open the vector dataset: {exc}") from exc

    layers: list[dict[str, Any]] = []
    total_layers = len(raw_layers)
    if total_layers == 0:
        raise DatasetInspectionError("The vector dataset contains no readable layers")

    for index, raw_layer in enumerate(raw_layers, start=1):
        layer_name = str(raw_layer[0])
        if progress is not None:
            progress(index, total_layers, layer_name)
        try:
            info = pyogrio.read_info(
                source,
                layer=layer_name,
                force_feature_count=True,
                force_total_bounds=True,
            )
        except Exception as exc:
            raise DatasetInspectionError(
                f"Could not inspect vector layer {layer_name}: {exc}"
            ) from exc

        fields = [
            {"name": str(name), "dtype": str(dtype)}
            for name, dtype in zip(info.get("fields", ()), info.get("dtypes", ()), strict=False)
        ]
        geometry_type = _optional_string(info.get("geometry_type"))
        crs = _optional_string(info.get("crs"))
        bounds = _number_list(info.get("total_bounds"))
        feature_count = int(info.get("features", -1))
        if geometry_type is None:
            warnings.append(f"Layer {layer_name} is nonspatial")
        elif crs is None:
            warnings.append(f"Layer {layer_name} has no coordinate reference system")
        if feature_count == 0:
            warnings.append(f"Layer {layer_name} contains no features")

        layers.append(
            {
                "name": layer_name,
                "driver": _optional_string(info.get("driver")),
                "geometry_type": geometry_type,
                "geometry_name": _optional_string(info.get("geometry_name")),
                "fid_column": _optional_string(info.get("fid_column")),
                "feature_count": feature_count,
                "crs": crs,
                "bounds": bounds,
                "encoding": _optional_string(info.get("encoding")),
                "fields": fields,
            }
        )

    result: dict[str, Any] = {
        "dataset_type": "vector",
        "format": _format_name(extension),
        "layer_count": len(layers),
        "layers": layers,
        "warnings": sorted(set(warnings)),
        "software": {
            "pyogrio": str(pyogrio.__version__),
            "gdal": str(pyogrio.__gdal_version_string__),
        },
    }
    if archive_result is not None:
        result["archive"] = {
            "member_count": archive_result.member_count,
            "compressed_size": archive_result.compressed_size,
            "uncompressed_size": archive_result.uncompressed_size,
            "shapefile_count": archive_result.shapefile_count,
        }
    return result


def _number_list(value: Any) -> list[float] | None:
    if value is None:
        return None
    return [float(item) for item in value]


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _format_name(extension: str) -> str:
    return {
        ".geojson": "GeoJSON",
        ".json": "GeoJSON",
        ".gpkg": "GeoPackage",
        ".zip": "Shapefile ZIP",
    }[extension]
