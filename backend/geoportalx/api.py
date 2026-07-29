from datetime import UTC, datetime

from django.db import connection
from ninja import NinjaAPI, Schema

from modules.datasets.api import router as datasets_router
from modules.jobs.api import router as jobs_router
from modules.maps.api import router as maps_router
from modules.organizations.api import router as organizations_router
from modules.raster_tiles.api import router as raster_tiles_router
from modules.resources.api import router as resources_router
from modules.uploads.api import router as uploads_router
from modules.vector_exports.api import router as vector_exports_router
from modules.vector_styles.api import router as vector_styles_router
from modules.vector_tiles.api import router as vector_tiles_router

api = NinjaAPI(title="GeoPortalX API", version="1.0.0", urls_namespace="api-v1")


class HealthResponse(Schema):
    status: str
    service: str
    database: str
    timestamp: datetime


@api.get("/health", response=HealthResponse, tags=["system"])
def health(request):
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchone()
    return {
        "status": "ok",
        "service": "geoportalx-backend",
        "database": "ok",
        "timestamp": datetime.now(UTC),
    }


api.add_router("/organizations", organizations_router)
api.add_router("/resources", resources_router)
api.add_router("/jobs", jobs_router)
api.add_router("/uploads", uploads_router)
api.add_router("/datasets", datasets_router)
api.add_router("/vector-layers", vector_tiles_router)
api.add_router("/vector-layers", vector_styles_router)
api.add_router("/vector-exports", vector_exports_router)
api.add_router("/raster-datasets", raster_tiles_router)
api.add_router("/maps", maps_router)
