"""Invoice Manager application configuration."""

from django.apps import AppConfig


class InvoiceManagerConfig(AppConfig):
    """Application config for Invoice Manager."""

    name = 'invoice_manager'
    verbose_name = 'Invoice Management'

    def ready(self):
        """Register signal handlers."""
