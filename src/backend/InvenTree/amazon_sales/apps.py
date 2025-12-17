"""App configuration for amazon_sales module."""

from django.apps import AppConfig


class AmazonSalesConfig(AppConfig):
    """App configuration for Amazon Sales importer."""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'amazon_sales'
    verbose_name = 'Amazon Sales'
