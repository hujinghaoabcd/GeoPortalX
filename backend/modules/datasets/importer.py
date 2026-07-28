import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from django.conf import settings
from django.contrib.gis.geos import Polygon
from django.db import connection, transaction

from .exceptions import VectorImportError
from .models import VectorLayer

_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


@dataclass(frozen=True, slots=True)
class ImportedLayerMetadata:
    db_schema: str
    db_table: str
    geometry_column: str
    geometry_type: str
    srid: int
    feature_count: int
    field_schema: list[dict[str, Any]]
    extent: Polygon | None


def import_vector_layer(*, source: Path, layer: VectorLayer) -> ImportedLayerMetadata:
    """Import one source layer through an isolated staging table."""

    schema = _validated_identifier(settings.DATASET_DB_SCHEMA, "dataset schema")
    staging_schema = _validated_identifier(
        settings.DATASET_STAGING_SCHEMA,
        "dataset staging schema",
    )
    staging_table = _validated_identifier(f"stg_{layer.id.hex}", "staging table")
    final_table = _validated_identifier(f"v_{layer.id.hex}", "dataset table")
    _ensure_schema(schema)
    _ensure_schema(staging_schema)
    _drop_table(staging_schema, staging_table)
    _drop_table(schema, final_table)

    source_name = _ogr_source(source, layer)
    try:
        _run_ogr2ogr(
            source=source_name,
            source_layer=layer.source_layer_name,
            target_schema=staging_schema,
            target_table=staging_table,
            transform_to_wgs84=bool(layer.source_crs),
        )
        metadata = _inspect_imported_table(staging_schema, staging_table)
        _ensure_spatial_index(
            schema=staging_schema,
            table=staging_table,
            geometry_column=metadata.geometry_column,
            layer_id=layer.id.hex,
        )
        _analyze_table(staging_schema, staging_table)
        _promote_staging_table(
            staging_schema=staging_schema,
            staging_table=staging_table,
            target_schema=schema,
            target_table=final_table,
        )
    except Exception:
        _drop_table(staging_schema, staging_table)
        _drop_table(schema, final_table)
        raise

    return ImportedLayerMetadata(
        db_schema=schema,
        db_table=final_table,
        geometry_column=metadata.geometry_column,
        geometry_type=metadata.geometry_type,
        srid=metadata.srid,
        feature_count=metadata.feature_count,
        field_schema=metadata.field_schema,
        extent=metadata.extent,
    )


def drop_vector_layer_storage(layer: VectorLayer) -> None:
    if layer.db_schema and layer.db_table:
        _drop_table(layer.db_schema, layer.db_table)
    staging_schema = _validated_identifier(
        settings.DATASET_STAGING_SCHEMA,
        "dataset staging schema",
    )
    _drop_table(staging_schema, f"stg_{layer.id.hex}")


def _ogr_source(source: Path, layer: VectorLayer) -> str:
    if layer.version.source_upload.original_filename.lower().endswith(".zip"):
        return f"/vsizip/{source.as_posix()}"
    return str(source)


def _run_ogr2ogr(
    *,
    source: str,
    source_layer: str,
    target_schema: str,
    target_table: str,
    transform_to_wgs84: bool,
) -> None:
    executable = settings.OGR2OGR_EXECUTABLE
    if shutil.which(executable) is None:
        raise VectorImportError(f"Required executable is unavailable: {executable}")

    command = [
        executable,
        "-f",
        "PostgreSQL",
        "-nln",
        f"{target_schema}.{target_table}",
        "-overwrite",
        "-nlt",
        "PROMOTE_TO_MULTI",
        "-lco",
        "GEOMETRY_NAME=geom",
        "-lco",
        "FID=gx_fid",
        "-lco",
        "LAUNDER=YES",
        "-lco",
        "SPATIAL_INDEX=NONE",
        "-gt",
        "65536",
    ]
    if transform_to_wgs84:
        command.extend(("-t_srs", "EPSG:4326"))
    command.extend(("PG:", source, source_layer))

    environment = os.environ.copy()
    environment.update(_postgres_environment())
    completed = subprocess.run(
        command,
        env=environment,
        text=True,
        capture_output=True,
        timeout=settings.VECTOR_IMPORT_TIMEOUT,
        check=False,
    )
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "ogr2ogr failed").strip()
        raise VectorImportError(message[-4000:])


def _postgres_environment() -> dict[str, str]:
    configuration = connection.settings_dict
    environment = {
        "PGDATABASE": str(configuration.get("NAME") or ""),
        "PGUSER": str(configuration.get("USER") or ""),
        "PGPASSWORD": str(configuration.get("PASSWORD") or ""),
        "PGHOST": str(configuration.get("HOST") or "localhost"),
        "PGPORT": str(configuration.get("PORT") or "5432"),
    }
    options = configuration.get("OPTIONS") or {}
    if options.get("sslmode"):
        environment["PGSSLMODE"] = str(options["sslmode"])
    return environment


def _inspect_imported_table(schema: str, table: str) -> ImportedLayerMetadata:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT f_geometry_column, type, srid
            FROM geometry_columns
            WHERE f_table_schema = %s AND f_table_name = %s
            """,
            [schema, table],
        )
        geometry = cursor.fetchone()
        if geometry is None:
            raise VectorImportError("Imported layer does not contain a PostGIS geometry column")
        geometry_column, geometry_type, srid = geometry
        quoted_table = _qualified_name(schema, table)
        quoted_geometry = connection.ops.quote_name(str(geometry_column))
        cursor.execute(f"SELECT COUNT(*) FROM {quoted_table}")
        feature_count = int(cursor.fetchone()[0])
        cursor.execute(
            """
            SELECT column_name, data_type, udt_name, is_nullable
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s AND column_name <> %s
            ORDER BY ordinal_position
            """,
            [schema, table, geometry_column],
        )
        field_schema = [
            {
                "name": str(name),
                "data_type": str(data_type),
                "database_type": str(database_type),
                "nullable": nullable == "YES",
            }
            for name, data_type, database_type, nullable in cursor.fetchall()
        ]
        extent = None
        if int(srid) == 4326 and feature_count:
            cursor.execute(
                f"""
                SELECT ST_XMin(bounds), ST_YMin(bounds), ST_XMax(bounds), ST_YMax(bounds)
                FROM (
                    SELECT ST_Extent({quoted_geometry})::box3d AS bounds
                    FROM {quoted_table}
                    WHERE {quoted_geometry} IS NOT NULL
                ) AS extent_query
                """
            )
            values = cursor.fetchone()
            if values and all(value is not None for value in values):
                extent = Polygon.from_bbox(tuple(float(value) for value in values))
                extent.srid = 4326

    return ImportedLayerMetadata(
        db_schema=schema,
        db_table=table,
        geometry_column=str(geometry_column),
        geometry_type=str(geometry_type),
        srid=int(srid),
        feature_count=feature_count,
        field_schema=field_schema,
        extent=extent,
    )


def _ensure_schema(schema: str) -> None:
    quoted = connection.ops.quote_name(_validated_identifier(schema, "schema"))
    with connection.cursor() as cursor:
        cursor.execute(f"CREATE SCHEMA IF NOT EXISTS {quoted}")


def _drop_table(schema: str, table: str) -> None:
    qualified = _qualified_name(schema, table)
    with connection.cursor() as cursor:
        cursor.execute(f"DROP TABLE IF EXISTS {qualified} CASCADE")


def _ensure_spatial_index(
    *,
    schema: str,
    table: str,
    geometry_column: str,
    layer_id: str,
) -> None:
    index_name = _validated_identifier(f"gx_{layer_id[:24]}_geom_gix", "spatial index")
    qualified_table = _qualified_name(schema, table)
    quoted_index = connection.ops.quote_name(index_name)
    quoted_geometry = connection.ops.quote_name(
        _validated_identifier(geometry_column, "geometry column")
    )
    with connection.cursor() as cursor:
        cursor.execute(
            f"CREATE INDEX {quoted_index} ON {qualified_table} USING GIST ({quoted_geometry})"
        )


def _analyze_table(schema: str, table: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute(f"ANALYZE {_qualified_name(schema, table)}")


@transaction.atomic
def _promote_staging_table(
    *,
    staging_schema: str,
    staging_table: str,
    target_schema: str,
    target_table: str,
) -> None:
    source = _qualified_name(staging_schema, staging_table)
    quoted_target_schema = connection.ops.quote_name(
        _validated_identifier(target_schema, "target schema")
    )
    quoted_target_table = connection.ops.quote_name(
        _validated_identifier(target_table, "target table")
    )
    with connection.cursor() as cursor:
        cursor.execute(f"ALTER TABLE {source} SET SCHEMA {quoted_target_schema}")
        moved = _qualified_name(target_schema, staging_table)
        cursor.execute(f"ALTER TABLE {moved} RENAME TO {quoted_target_table}")


def _qualified_name(schema: str, table: str) -> str:
    schema = _validated_identifier(schema, "schema")
    table = _validated_identifier(table, "table")
    return f"{connection.ops.quote_name(schema)}.{connection.ops.quote_name(table)}"


def _validated_identifier(value: str, label: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise VectorImportError(f"Invalid {label}: {value}")
    return value
