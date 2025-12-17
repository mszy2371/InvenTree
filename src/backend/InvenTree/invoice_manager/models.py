"""Django models for invoice management."""

from django.contrib.auth.models import User
from django.db import models

from company.models import Company
from part.models import Part


class Invoice(models.Model):
    """Model representing an invoice."""

    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('PROCESSING', 'Processing'),
        ('COMPLETED', 'Completed'),
        ('FAILED', 'Failed'),
        ('CANCELLED', 'Cancelled'),
    ]

    # Basic info
    invoice_number = models.CharField(max_length=100, unique=True)
    invoice_date = models.DateField()
    supplier = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name='invoices'
    )
    uploaded_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, related_name='uploaded_invoices'
    )

    # Invoice document
    invoice_file = models.FileField(upload_to='invoices/%Y/%m/')

    # Extracted data
    invoice_data = models.JSONField(
        default=dict, help_text='Raw extracted invoice data'
    )

    # Financial info
    total_net_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_vat_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    invoice_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    # Processing info
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    processing_log = models.TextField(blank=True, help_text='Log of processing steps')
    error_message = models.TextField(
        blank=True, help_text='Error details if processing failed'
    )

    # Purchase Order link
    purchase_order = models.ForeignKey(
        'order.PurchaseOrder',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='invoices',
        help_text='Purchase Order created from this invoice',
    )

    # Tracking
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        """Meta options for Invoice."""

        ordering = ['-created_at']
        verbose_name = 'Invoice'
        verbose_name_plural = 'Invoices'

    def __str__(self):
        """Return string representation."""
        return f'{self.invoice_number} - {self.supplier.name}'

    def get_missing_parts(self):
        """Get list of products that don't have matching parts."""
        missing = []
        for item in self.items.all():
            if not item.part:
                missing.append(item)
        return missing

    def can_process(self):
        """Check if invoice has all required parts."""
        return len(self.get_missing_parts()) == 0


class InvoiceItem(models.Model):
    """Model representing a line item in an invoice."""

    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='items')
    part = models.ForeignKey(Part, on_delete=models.SET_NULL, null=True, blank=True)

    # Item details from invoice
    description = models.TextField()
    seller_sku = models.CharField(max_length=100, blank=True, null=True)
    quantity = models.PositiveIntegerField()
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    total_price = models.DecimalField(max_digits=12, decimal_places=2)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=20)

    # Status
    matched = models.BooleanField(
        default=False, help_text='Whether part has been matched'
    )
    notes = models.TextField(blank=True)

    class Meta:
        """Meta options for InvoiceItem."""

        ordering = ['invoice', 'id']
        unique_together = [['invoice', 'seller_sku', 'description']]

    def __str__(self):
        """Return string representation."""
        part_str = f'[{self.part.name}]' if self.part else '[NO PART]'
        return f'{self.invoice.invoice_number} - {part_str} x{self.quantity}'

    def match_part(self, part: Part):
        """Match this item to a part."""
        self.part = part
        self.matched = True
        self.save()

    def create_stock_entry(self):
        """Create stock entry for this item (assumes part is matched)."""
        if not self.part or not self.matched:
            raise ValueError('Cannot create stock entry: part not matched')

        from stock.models import StockItem

        stock_item = StockItem.objects.create(
            part=self.part,
            quantity=self.quantity,
            location=self.part.get_default_location(),
            notes=f'Added from invoice {self.invoice.invoice_number}',
            batch=getattr(self.invoice, 'invoice_number', ''),
        )
        return stock_item


class InvoiceProcessingLog(models.Model):
    """Log of invoice processing actions."""

    ACTIONS = [
        ('UPLOAD', 'Upload'),
        ('EXTRACT', 'Extract'),
        ('MATCH', 'Match Part'),
        ('PO_CREATE', 'Purchase Order Created'),
        ('STOCK_CREATE', 'Stock Created'),
        ('STOCK_ERROR', 'Stock Error'),
        ('ERROR', 'Error'),
    ]

    invoice = models.ForeignKey(
        Invoice, on_delete=models.CASCADE, related_name='processing_logs'
    )
    action = models.CharField(max_length=20, choices=ACTIONS)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """Meta options for InvoiceProcessingLog."""

        ordering = ['-created_at']

    def __str__(self):
        """Return string representation."""
        return f'{self.invoice.invoice_number} - {self.action} at {self.created_at}'
