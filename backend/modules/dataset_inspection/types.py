from dataclasses import dataclass

from modules.object_storage.keys import safe_extension

from .exceptions import UnsupportedDatasetFormat


@dataclass(frozen=True, slots=True)
class InspectionJobSpec:
    job_type: str
    queue: str


def inspection_job_for_filename(filename: str) -> InspectionJobSpec:
    extension = safe_extension(filename)
    if extension in {".geojson", ".json", ".gpkg", ".zip"}:
        return InspectionJobSpec(job_type="vector-inspect", queue="vector")
    if extension in {".tif", ".tiff"}:
        return InspectionJobSpec(job_type="raster-inspect", queue="raster")
    raise UnsupportedDatasetFormat(
        "Inspection supports GeoJSON, GeoPackage, Shapefile ZIP and GeoTIFF uploads"
    )
