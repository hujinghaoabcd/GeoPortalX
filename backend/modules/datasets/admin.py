from django.contrib import admin

from .models import Dataset, DatasetVersion, RasterDataset, VectorDataset, VectorLayer

admin.site.register(Dataset)
admin.site.register(DatasetVersion)
admin.site.register(VectorDataset)
admin.site.register(VectorLayer)
admin.site.register(RasterDataset)
