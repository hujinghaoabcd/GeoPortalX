import csv
import json
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.db import connection

from modules.datasets.models import VectorLayerStatus
from modules.vector_exports.exporter import generate_vector_export
from modules.vector_exports.models import VectorExportFormat


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("export_format", "extension"),
    [
        (VectorExportFormat.GEOJSON, ".geojson"),
        (VectorExportFormat.CSV, ".csv"),
        (VectorExportFormat.GEOPACKAGE, ".gpkg"),
    ],
)
def test_real_ogr2ogr_vector_exports(export_format: str, extension: str) -> None:
    schema = "geoportalx_data"
    table = f"v_{uuid4().hex}"
    quoted_table = f'"{schema}"."{table}"'
    with connection.cursor() as cursor:
        cursor.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        cursor.execute(
            f"""
            CREATE TABLE {quoted_table} (
                gx_fid integer PRIMARY KEY,
                name text,
                speed integer,
                geom geometry(Point, 4326) NOT NULL
            )
            """
        )
        cursor.execute(
            f"""
            INSERT INTO {quoted_table} (gx_fid, name, speed, geom)
            VALUES
                (0, 'First', 30, ST_SetSRID(ST_MakePoint(118.5, 32.0), 4326)),
                (1, 'Second', 50, ST_SetSRID(ST_MakePoint(118.8, 32.2), 4326)),
                (2, 'Outside', 70, ST_SetSRID(ST_MakePoint(120.0, 35.0), 4326))
            """
        )

    layer = SimpleNamespace(
        status=VectorLayerStatus.READY,
        db_schema=schema,
        db_table=table,
        geometry_column="geom",
        srid=4326,
        field_schema=[
            {"name": "gx_fid"},
            {"name": "name"},
            {"name": "speed"},
        ],
        title="Road samples",
    )
    export = SimpleNamespace(
        id=uuid4(),
        export_format=export_format,
        layer=layer,
        selected_fields=["name", "speed"],
        bbox=[118.0, 31.0, 119.0, 33.0],
    )
    try:
        with TemporaryDirectory() as directory:
            generated = generate_vector_export(
                export=export,
                workspace=Path(directory),
                cancel_check=lambda: None,
            )
            assert generated.path.suffix == extension
            assert generated.size > 0
            _assert_export_content(generated.path, export_format)
    finally:
        with connection.cursor() as cursor:
            cursor.execute(f"DROP TABLE IF EXISTS {quoted_table}")


def _assert_export_content(path: Path, export_format: str) -> None:
    if export_format == VectorExportFormat.GEOJSON:
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["type"] == "FeatureCollection"
        assert [feature["properties"]["name"] for feature in payload["features"]] == [
            "First",
            "Second",
        ]
        return

    if export_format == VectorExportFormat.CSV:
        with path.open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        assert [row["name"] for row in rows] == ["First", "Second"]
        assert all(row.get("WKT") for row in rows)
        return

    with sqlite3.connect(path) as database:
        contents = database.execute(
            "SELECT table_name FROM gpkg_contents WHERE data_type = 'features'"
        ).fetchone()
        assert contents is not None
        count = database.execute(f'SELECT COUNT(*) FROM "{contents[0]}"').fetchone()[0]
        assert count == 2
