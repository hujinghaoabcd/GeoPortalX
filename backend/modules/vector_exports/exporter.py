import os
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.db import connection
from django.utils.text import slugify

from modules.datasets.models import VectorLayerStatus

from .models import VectorExport, VectorExportFormat


class VectorExportError(RuntimeError):
    """Raised when a canonical PostGIS layer cannot be exported safely."""


@dataclass(frozen=True, slots=True)
class GeneratedVectorExport:
    path: Path
    filename: str
    content_type: str
    size: int


@dataclass(frozen=True, slots=True)
class _FormatSpec:
    driver: str
    extension: str
    content_type: str
    layer_options: tuple[str, ...]


_FORMAT_SPECS = {
    VectorExportFormat.GEOJSON: _FormatSpec(
        driver="GeoJSON",
        extension="geojson",
        content_type="application/geo+json",
        layer_options=("RFC7946=YES", "WRITE_BBOX=YES"),
    ),
    VectorExportFormat.CSV: _FormatSpec(
        driver="CSV",
        extension="csv",
        content_type="text/csv; charset=utf-8",
        layer_options=(),
    ),
    VectorExportFormat.GEOPACKAGE: _FormatSpec(
        driver="GPKG",
        extension="gpkg",
        content_type="application/geopackage+sqlite3",
        layer_options=("SPATIAL_INDEX=YES",),
    ),
}


def result_filename_for(export: VectorExport) -> str:
    spec = _format_spec(export.export_format)
    base = slugify(export.layer.title, allow_unicode=False)[:60] or "vector-layer"
    return f"{base}-{export.id.hex[:8]}.{spec.extension}"


def object_key_for(export: VectorExport) -> str:
    spec = _format_spec(export.export_format)
    return (
        f"exports/{export.created_by_id}/{export.id}/"
        f"{export.id}.{spec.extension}"
    )


def generate_vector_export(
    *,
    export: VectorExport,
    workspace: Path,
    cancel_check: Callable[[], None],
) -> GeneratedVectorExport:
    layer = export.layer
    if layer.status != VectorLayerStatus.READY:
        raise VectorExportError("Vector layer is not ready")
    if not layer.db_schema or not layer.db_table or not layer.geometry_column:
        raise VectorExportError("Vector layer storage metadata is incomplete")
    if layer.srid != 4326:
        raise VectorExportError("Vector export currently requires an EPSG:4326 layer")

    executable = str(settings.OGR2OGR_EXECUTABLE)
    if shutil.which(executable) is None:
        raise VectorExportError(f"Required executable is unavailable: {executable}")

    spec = _format_spec(export.export_format)
    workspace = Path(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    filename = result_filename_for(export)
    destination = workspace / filename
    command = _build_command(export=export, destination=destination, spec=spec)
    _run_command(command, cancel_check=cancel_check)

    if not destination.is_file():
        raise VectorExportError("ogr2ogr did not create the expected export file")
    size = destination.stat().st_size
    maximum = max(int(settings.VECTOR_EXPORT_MAX_BYTES), 1)
    if size <= 0:
        raise VectorExportError("The generated export file is empty")
    if size > maximum:
        destination.unlink(missing_ok=True)
        raise VectorExportError(
            f"Generated export exceeds the configured {maximum}-byte limit"
        )
    return GeneratedVectorExport(
        path=destination,
        filename=filename,
        content_type=spec.content_type,
        size=size,
    )


def _build_command(
    *,
    export: VectorExport,
    destination: Path,
    spec: _FormatSpec,
) -> list[str]:
    layer = export.layer
    available = {
        str(field.get("name", ""))
        for field in layer.field_schema
        if str(field.get("name", ""))
    }
    selected = list(export.selected_fields)
    unknown = sorted(set(selected) - available)
    if unknown:
        raise VectorExportError(f"Export fields are unavailable: {', '.join(unknown)}")

    fid = _quote_identifier("gx_fid")
    geometry = _quote_identifier(layer.geometry_column)
    selected_sql = [f"t.{fid}"]
    selected_sql.extend(f"t.{_quote_identifier(value)}" for value in selected)
    if export.export_format == VectorExportFormat.CSV:
        selected_sql.append(f"ST_AsText(t.{geometry}) AS {_quote_identifier('WKT')}")
    else:
        selected_sql.append(f"t.{geometry}")

    table_sql = (
        f"{_quote_identifier(layer.db_schema)}."
        f"{_quote_identifier(layer.db_table)}"
    )
    sql = f"SELECT {', '.join(selected_sql)} FROM {table_sql} AS t"
    bbox_in_sql = export.export_format == VectorExportFormat.CSV and export.bbox
    if bbox_in_sql:
        envelope = _bbox_envelope_sql(export.bbox)
        sql += (
            f" WHERE t.{geometry} && {envelope}"
            f" AND ST_Intersects(t.{geometry}, {envelope})"
        )
    sql += f" ORDER BY t.{fid} ASC"

    command = [
        str(settings.OGR2OGR_EXECUTABLE),
        "-f",
        spec.driver,
        "-overwrite",
        "-sql",
        sql,
        "-dialect",
        "PostgreSQL",
        "-nln",
        "layer",
    ]
    for option in spec.layer_options:
        command.extend(("-lco", option))
    if export.bbox and not bbox_in_sql:
        command.extend(("-spat", *(str(value) for value in export.bbox)))
    command.extend((str(destination), "PG:"))
    return command


def _run_command(command: list[str], *, cancel_check: Callable[[], None]) -> None:
    process = subprocess.Popen(
        command,
        env=_postgres_environment(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + max(int(settings.VECTOR_EXPORT_TIMEOUT), 1)
    try:
        while True:
            cancel_check()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise VectorExportError("Vector export timed out")
            try:
                stdout, stderr = process.communicate(timeout=min(0.5, remaining))
                break
            except subprocess.TimeoutExpired:
                continue
    except BaseException:
        process.terminate()
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
        raise

    if process.returncode != 0:
        message = (stderr or stdout or "ogr2ogr failed").strip()
        raise VectorExportError(message[-4000:])


def _bbox_envelope_sql(bbox: list[float]) -> str:
    if len(bbox) != 4:
        raise VectorExportError("Export bbox must contain four coordinates")
    values = ", ".join(format(float(value), ".15g") for value in bbox)
    return f"ST_MakeEnvelope({values}, 4326)"


def _postgres_environment() -> dict[str, str]:
    configuration = connection.settings_dict
    environment = os.environ.copy()
    environment.update(
        {
            "PGDATABASE": str(configuration.get("NAME") or ""),
            "PGUSER": str(configuration.get("USER") or ""),
            "PGPASSWORD": str(configuration.get("PASSWORD") or ""),
            "PGHOST": str(configuration.get("HOST") or "localhost"),
            "PGPORT": str(configuration.get("PORT") or "5432"),
        }
    )
    options = configuration.get("OPTIONS") or {}
    if options.get("sslmode"):
        environment["PGSSLMODE"] = str(options["sslmode"])
    return environment


def _quote_identifier(value: str) -> str:
    if not value or "\x00" in value or len(value) > 63:
        raise VectorExportError("Database identifier is invalid")
    return f'"{value.replace(chr(34), chr(34) * 2)}"'


def _format_spec(export_format: str) -> _FormatSpec:
    try:
        return _FORMAT_SPECS[export_format]
    except KeyError as exc:
        raise VectorExportError(f"Unsupported vector export format: {export_format}") from exc
