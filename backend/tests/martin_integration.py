import json
import os
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen

import psycopg


def setup_database() -> None:
    database_url = os.environ["MARTIN_TEST_DATABASE_URL"]
    with psycopg.connect(database_url, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute("CREATE EXTENSION IF NOT EXISTS postgis")
            cursor.execute("CREATE SCHEMA IF NOT EXISTS geoportalx_data")
            cursor.execute("DROP TABLE IF EXISTS geoportalx_data.v_integration")
            cursor.execute(
                """
                CREATE TABLE geoportalx_data.v_integration (
                    gx_fid bigserial PRIMARY KEY,
                    name text,
                    geom geometry(Point, 4326) NOT NULL
                )
                """
            )
            cursor.execute(
                """
                INSERT INTO geoportalx_data.v_integration (name, geom)
                VALUES ('origin', ST_SetSRID(ST_MakePoint(0, 0), 4326))
                """
            )
            cursor.execute(
                """
                CREATE INDEX v_integration_geom_gix
                ON geoportalx_data.v_integration
                USING GIST (geom)
                """
            )
            cursor.execute("ANALYZE geoportalx_data.v_integration")


def check_martin() -> None:
    base_url = os.environ.get("MARTIN_TEST_URL", "http://127.0.0.1:3000")
    for _attempt in range(30):
        try:
            with urlopen(f"{base_url}/health", timeout=2) as response:
                if response.status == 200:
                    break
        except (OSError, URLError):
            time.sleep(1)
    else:
        raise RuntimeError("Martin did not become healthy")

    with urlopen(f"{base_url}/v_integration", timeout=5) as response:
        tilejson = json.load(response)
    assert tilejson["tilejson"] == "3.0.0"
    assert tilejson["vector_layers"][0]["id"] == "v_integration"
    assert tilejson["vector_layers"][0]["fields"]["name"]

    with urlopen(f"{base_url}/v_integration/0/0/0", timeout=10) as response:
        tile = response.read()
        assert response.status in {200, 204}
        if response.status == 200:
            assert tile


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "setup":
        setup_database()
    elif command == "check":
        check_martin()
    else:
        raise SystemExit("Usage: martin_integration.py [setup|check]")
