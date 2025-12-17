"""URLs for admin import supplier products."""

from django.contrib.auth.decorators import login_required
from django.urls import path

from .admin_actions import SupplierProductImporterAdmin

# Stwórz instancję admin klasy
importer_admin = SupplierProductImporterAdmin()
importer_admin.site_header = 'InvenTree Admin'

urlpatterns = [
    path(
        '',
        login_required(importer_admin.import_supplier_products_view),
        name='import_supplier_products',
    )
]
