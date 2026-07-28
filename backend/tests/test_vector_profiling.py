from uuid import uuid4

import pytest
from django.db import connection

from modules.datasets.profiling import profile_vector_table


@pytest.mark.django_db(transaction=True)
def test_vector_profile_reports_geometry_quality_and_field_statistics(settings) -> None:
    settings.VECTOR_PROFILE_SAMPLE_SIZE = 100
    settings.VECTOR_PROFILE_MAX_FIELDS = 10
    settings.VECTOR_PROFILE_TOP_VALUES = 5
    settings.VECTOR_PROFILE_TOP_VALUES_MAX_DISTINCT = 100

    schema = "geoportalx_profile_test"
    table = f"v_{uuid4().hex}"
    qualified = f'"{schema}"."{table}"'
    with connection.cursor() as cursor:
        cursor.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        cursor.execute(
            f"""
            CREATE TABLE {qualified} (
                gx_fid bigserial PRIMARY KEY,
                name text,
                lanes integer,
                geom geometry(Geometry, 4326)
            )
            """
        )
        cursor.execute(
            f"""
            INSERT INTO {qualified} (name, lanes, geom)
            VALUES
                ('Main Street', 2, ST_GeomFromText('POINT(0 0)', 4326)),
                (
                    'Broken polygon',
                    4,
                    ST_GeomFromText(
                        'POLYGON((0 0, 1 1, 1 0, 0 1, 0 0))',
                        4326
                    )
                ),
                ('Empty', NULL, ST_GeomFromText('GEOMETRYCOLLECTION EMPTY', 4326)),
                (NULL, NULL, NULL)
            """
        )

    try:
        profile = profile_vector_table(
            schema=schema,
            table=table,
            geometry_column="geom",
            field_schema=[
                {
                    "name": "gx_fid",
                    "data_type": "bigint",
                    "database_type": "int8",
                    "nullable": False,
                },
                {
                    "name": "name",
                    "data_type": "text",
                    "database_type": "text",
                    "nullable": True,
                },
                {
                    "name": "lanes",
                    "data_type": "integer",
                    "database_type": "int4",
                    "nullable": True,
                },
            ],
            feature_count=4,
        )
    finally:
        with connection.cursor() as cursor:
            cursor.execute(f"DROP TABLE IF EXISTS {qualified} CASCADE")

    quality = profile.quality_report
    assert quality["sample_size"] == 4
    assert quality["sampled"] is False
    assert quality["null_geometry_count"] == 1
    assert quality["empty_geometry_count"] == 1
    assert quality["invalid_geometry_count"] == 1
    assert quality["valid_geometry_count"] == 1
    assert quality["srids"] == {"4326": 2}
    assert quality["invalid_reasons"]

    fields = {field["name"]: field for field in profile.field_statistics}
    assert set(fields) == {"name", "lanes"}
    assert fields["name"]["null_count"] == 1
    assert fields["name"]["distinct_count"] == 3
    assert fields["lanes"]["minimum"] == 2
    assert fields["lanes"]["maximum"] == 4
    assert fields["lanes"]["average"] == 3.0
