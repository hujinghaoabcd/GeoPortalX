from functools import partial

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import RasterDataset
from .raster_services import ensure_initial_raster_publication


@receiver(post_save, sender=RasterDataset)
def queue_initial_raster_publication(
    sender,
    instance: RasterDataset,
    created: bool,
    **kwargs,
) -> None:
    if created:
        transaction.on_commit(partial(ensure_initial_raster_publication, instance.dataset_id))
