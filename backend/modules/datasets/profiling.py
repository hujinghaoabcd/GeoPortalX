import re
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.db import connection

from .exceptions import VectorImportError

_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_NUMERIC_TYPES = {
    "bigint",
    "decimal",
    "double precision",
    "integer",
    "numeric",
    "real",
    "smallint",
}
_NUMERIC_UDTS = {"float4", "float8", "int2", "int4", "int8", "numeric"}
_TEMPORAL_TYPES = {
    "date",
    "time with time zone",
    "time without time zone",
    "timestamp with time zone",
    "timestamp without time zone",
}
_BOOLEAN_TYPES = {"boolean"}
_BOOLEAN_UDTS = {"bool"}


@dataclass(frozen=True, slots=True)
class VectorProfile:
    quality_report: dict[str, Any]
    field_statistics: list[dict[str, Any]]


def profile_vector_table(
    *,
    schema: str,
    table: str,
    geometry_column: str,
    field_schema: list[dict[str, Any]],
    feature_count: int,
) -> VectorProfile:
    """Build a bounded profile without loading features into Python."""

    sample_limit = max(int(settings.VECTOR_PROFILE_SAMPLE_SIZE), 1)
    sample_size = min(max(feature_count, 0), sample_limit)
    if sample_size == 0:
        return VectorProfile(
            quality_report={
                "total_feature_count": feature_count,
                "sample_size": 0,
                "sampled": False,
                "sampling_method": "table-prefix",
                "null_geometry_count": 0,
                "empty_geometry_count": 0,
                "invalid_geometry_count": 0,
                "valid_geometry_count": 0,
                "geometry_types": {},
                "srids": {},
                "dimensions": [],
                "invalid_reasons": [],
            },
            field_statistics=[],
        )

    qualified_table = _qualified_name(schema, table)
    quoted_geometry = _quoted_identifier(geometry_column, "geometry column")
    quality_report = _profile_geometry(
        qualified_table=qualified_table,
        quoted_geometry=quoted_geometry,
        sample_size=sample_size,
        feature_count=feature_count,
    )
    field_statistics = _profile_fields(
        qualified_table=qualified_table,
        field_schema=field_schema,
        sample_size=sample_size,
    )
    return VectorProfile(
        quality_report=quality_report,
        field_statistics=field_statistics,
    )


def _profile_geometry(
    *,
    qualified_table: str,
    quoted_geometry: str,
    sample_size: int,
    feature_count: int,
) -> dict[str, Any]:
    sample_cte = f"WITH sample AS (SELECT * FROM {qualified_table} LIMIT %s)"
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            {sample_cte}
            SELECT
                COUNT(*)::bigint,
                COUNT(*) FILTER (WHERE {quoted_geometry} IS NULL)::bigint,
                COUNT(*) FILTER (
                    WHERE {quoted_geometry} IS NOT NULL
                      AND ST_IsEmpty({quoted_geometry})
                )::bigint,
                COUNT(*) FILTER (
                    WHERE {quoted_geometry} IS NOT NULL
                      AND NOT ST_IsEmpty({quoted_geometry})
                      AND NOT ST_IsValid({quoted_geometry})
                )::bigint,
                COUNT(*) FILTER (
                    WHERE {quoted_geometry} IS NOT NULL
                      AND NOT ST_IsEmpty({quoted_geometry})
                      AND ST_IsValid({quoted_geometry})
                )::bigint,
                MIN(ST_NDims({quoted_geometry})) FILTER (
                    WHERE {quoted_geometry} IS NOT NULL
                      AND NOT ST_IsEmpty({quoted_geometry})
                ),
                MAX(ST_NDims({quoted_geometry})) FILTER (
                    WHERE {quoted_geometry} IS NOT NULL
                      AND NOT ST_IsEmpty({quoted_geometry})
                )
            FROM sample
            """,
            [sample_size],
        )
        (
            actual_sample_size,
            null_count,
            empty_count,
            invalid_count,
            valid_count,
            min_dimensions,
            max_dimensions,
        ) = cursor.fetchone()

        cursor.execute(
            f"""
            {sample_cte}
            SELECT ST_GeometryType({quoted_geometry}), COUNT(*)::bigint
            FROM sample
            WHERE {quoted_geometry} IS NOT NULL
              AND NOT ST_IsEmpty({quoted_geometry})
            GROUP BY ST_GeometryType({quoted_geometry})
            ORDER BY ST_GeometryType({quoted_geometry})
            """,
            [sample_size],
        )
        geometry_types = {
            str(name).removeprefix("ST_"): int(count)
            for name, count in cursor.fetchall()
            if name is not None
        }

        cursor.execute(
            f"""
            {sample_cte}
            SELECT ST_SRID({quoted_geometry}), COUNT(*)::bigint
            FROM sample
            WHERE {quoted_geometry} IS NOT NULL
              AND NOT ST_IsEmpty({quoted_geometry})
            GROUP BY ST_SRID({quoted_geometry})
            ORDER BY ST_SRID({quoted_geometry})
            """,
            [sample_size],
        )
        srids = {str(int(srid)): int(count) for srid, count in cursor.fetchall()}

        cursor.execute(
            f"""
            {sample_cte}
            SELECT ST_IsValidReason({quoted_geometry}), COUNT(*)::bigint
            FROM sample
            WHERE {quoted_geometry} IS NOT NULL
              AND NOT ST_IsEmpty({quoted_geometry})
              AND NOT ST_IsValid({quoted_geometry})
            GROUP BY ST_IsValidReason({quoted_geometry})
            ORDER BY COUNT(*) DESC, ST_IsValidReason({quoted_geometry})
            LIMIT 10
            """,
            [sample_size],
        )
        invalid_reasons = [
            {"reason": str(reason), "count": int(count)}
            for reason, count in cursor.fetchall()
        ]

    dimensions: list[int] = []
    if min_dimensions is not None:
        dimensions.append(int(min_dimensions))
    if max_dimensions is not None and max_dimensions != min_dimensions:
        dimensions.append(int(max_dimensions))

    return {
        "total_feature_count": int(feature_count),
        "sample_size": int(actual_sample_size),
        "sampled": feature_count > actual_sample_size,
        "sampling_method": "table-prefix",
        "null_geometry_count": int(null_count),
        "empty_geometry_count": int(empty_count),
        "invalid_geometry_count": int(invalid_count),
        "valid_geometry_count": int(valid_count),
        "geometry_types": geometry_types,
        "srids": srids,
        "dimensions": dimensions,
        "invalid_reasons": invalid_reasons,
    }


def _profile_fields(
    *,
    qualified_table: str,
    field_schema: list[dict[str, Any]],
    sample_size: int,
) -> list[dict[str, Any]]:
    maximum_fields = max(int(settings.VECTOR_PROFILE_MAX_FIELDS), 0)
    fields = [
        field
        for field in field_schema
        if str(field.get("name", "")) not in {"", "gx_fid"}
    ][:maximum_fields]
    return [
        _profile_field(
            qualified_table=qualified_table,
            field=field,
            sample_size=sample_size,
        )
        for field in fields
    ]


def _profile_field(
    *,
    qualified_table: str,
    field: dict[str, Any],
    sample_size: int,
) -> dict[str, Any]:
    name = str(field["name"])
    quoted_column = _quoted_identifier(name, "field")
    data_type = str(field.get("data_type") or "")
    database_type = str(field.get("database_type") or "")
    sample_cte = f"WITH sample AS (SELECT * FROM {qualified_table} LIMIT %s)"

    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            {sample_cte}
            SELECT
                COUNT(*)::bigint,
                COUNT(*) FILTER (WHERE {quoted_column} IS NULL)::bigint,
                COUNT(DISTINCT {quoted_column}::text)::bigint
            FROM sample
            """,
            [sample_size],
        )
        sampled_rows, null_count, distinct_count = cursor.fetchone()

        statistics: dict[str, Any] = {
            "name": name,
            "data_type": data_type,
            "database_type": database_type,
            "sample_size": int(sampled_rows),
            "null_count": int(null_count),
            "non_null_count": int(sampled_rows - null_count),
            "distinct_count": int(distinct_count),
        }

        normalized_data_type = data_type.lower()
        normalized_database_type = database_type.lower()
        if (
            normalized_data_type in _NUMERIC_TYPES
            or normalized_database_type in _NUMERIC_UDTS
        ):
            cursor.execute(
                f"""
                {sample_cte}
                SELECT
                    MIN({quoted_column}),
                    MAX({quoted_column}),
                    AVG({quoted_column}::double precision)
                FROM sample
                WHERE {quoted_column} IS NOT NULL
                """,
                [sample_size],
            )
            minimum, maximum, average = cursor.fetchone()
            statistics["minimum"] = _json_value(minimum)
            statistics["maximum"] = _json_value(maximum)
            statistics["average"] = _json_value(average)
        elif (
            normalized_data_type in _TEMPORAL_TYPES
            or normalized_database_type in {"date", "time", "timetz", "timestamp", "timestamptz"}
        ):
            cursor.execute(
                f"""
                {sample_cte}
                SELECT MIN({quoted_column})::text, MAX({quoted_column})::text
                FROM sample
                WHERE {quoted_column} IS NOT NULL
                """,
                [sample_size],
            )
            minimum, maximum = cursor.fetchone()
            statistics["minimum"] = minimum
            statistics["maximum"] = maximum
        elif (
            normalized_data_type in _BOOLEAN_TYPES
            or normalized_database_type in _BOOLEAN_UDTS
        ):
            cursor.execute(
                f"""
                {sample_cte}
                SELECT
                    COUNT(*) FILTER (WHERE {quoted_column} IS TRUE)::bigint,
                    COUNT(*) FILTER (WHERE {quoted_column} IS FALSE)::bigint
                FROM sample
                """,
                [sample_size],
            )
            true_count, false_count = cursor.fetchone()
            statistics["true_count"] = int(true_count)
            statistics["false_count"] = int(false_count)
        else:
            cursor.execute(
                f"""
                {sample_cte}
                SELECT
                    MIN(LENGTH({quoted_column}::text)),
                    MAX(LENGTH({quoted_column}::text)),
                    AVG(LENGTH({quoted_column}::text))
                FROM sample
                WHERE {quoted_column} IS NOT NULL
                """,
                [sample_size],
            )
            minimum_length, maximum_length, average_length = cursor.fetchone()
            statistics["minimum_length"] = _json_value(minimum_length)
            statistics["maximum_length"] = _json_value(maximum_length)
            statistics["average_length"] = _json_value(average_length)

        top_value_limit = max(int(settings.VECTOR_PROFILE_TOP_VALUES), 0)
        distinct_threshold = max(
            int(settings.VECTOR_PROFILE_TOP_VALUES_MAX_DISTINCT),
            0,
        )
        if (
            top_value_limit > 0
            and statistics["non_null_count"] > 0
            and distinct_count <= distinct_threshold
        ):
            cursor.execute(
                f"""
                {sample_cte}
                SELECT {quoted_column}::text, COUNT(*)::bigint
                FROM sample
                WHERE {quoted_column} IS NOT NULL
                GROUP BY {quoted_column}::text
                ORDER BY COUNT(*) DESC, {quoted_column}::text
                LIMIT %s
                """,
                [sample_size, top_value_limit],
            )
            statistics["top_values"] = [
                {"value": value, "count": int(count)}
                for value, count in cursor.fetchall()
            ]

    return statistics


def _json_value(value: Any) -> int | float | str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, (float, Decimal)):
        return float(value)
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    return str(value)


def _qualified_name(schema: str, table: str) -> str:
    return (
        f"{_quoted_identifier(schema, 'schema')}."
        f"{_quoted_identifier(table, 'table')}"
    )


def _quoted_identifier(value: str, label: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise VectorImportError(f"Invalid {label}: {value}")
    return connection.ops.quote_name(value)
