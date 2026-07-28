from django.db.models.signals import post_save
from django.dispatch import receiver

from modules.datasets.models import VectorLayer, VectorLayerStatus

from .models import VectorStyle
from .services import default_symbol_for_geometry


@receiver(post_save, sender=VectorLayer, dispatch_uid="vector_styles_ready_layer")
def ensure_ready_layer_style(sender, instance: VectorLayer, **kwargs) -> None:
    if instance.status != VectorLayerStatus.READY:
        return
    VectorStyle.objects.get_or_create(
        layer=instance,
        defaults={
            "symbol": default_symbol_for_geometry(instance.geometry_type),
            "fallback_symbol": {"color": "#9ca3af", "opacity": 0.65},
        },
    )
