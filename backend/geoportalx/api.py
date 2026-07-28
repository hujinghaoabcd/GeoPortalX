from datetime import UTC, datetime

from django.db import connection
from ninja import NinjaAPI, Schema

from modules.organizations.api import router as organizations_router
from modules.resources.api import router as resources_router

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
