"""Management command to process invoices."""

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from invoice_manager.extractors import get_extractor_for_supplier
from invoice_manager.models import Invoice, InvoiceItem, InvoiceProcessingLog
from part.models import Part


class Command(BaseCommand):
    """Django command to process invoices."""

    help = 'Process invoices and extract data'

    def add_arguments(self, parser):
        """Add command arguments."""
        parser.add_argument(
            '--invoice-id', type=int, help='Process specific invoice by ID'
        )
        parser.add_argument(
            '--auto-match',
            action='store_true',
            help='Automatically match parts after extraction',
        )
        parser.add_argument(
            '--create-stock',
            action='store_true',
            help='Create stock entries for matched items',
        )
        parser.add_argument(
            '--pending-only',
            action='store_true',
            default=True,
            help='Process only pending invoices (default: True)',
        )

    def handle(self, *args, **options):
        """Execute the command."""
        if options['invoice_id']:
            invoices = Invoice.objects.filter(id=options['invoice_id'])
            if not invoices.exists():
                raise CommandError(f'Invoice with ID {options["invoice_id"]} not found')
        else:
            if options['pending_only']:
                invoices = Invoice.objects.filter(status='PENDING')
            else:
                invoices = Invoice.objects.all()

        self.stdout.write(
            self.style.SUCCESS(f'Processing {invoices.count()} invoices...')
        )

        for invoice in invoices:
            self.process_invoice(invoice, options)

    def process_invoice(self, invoice: Invoice, options):
        """Process a single invoice."""
        try:
            self.stdout.write(f'\nProcessing invoice: {invoice.invoice_number}')

            # Extract data
            self.stdout.write('  Extracting data from PDF...')
            extractor = get_extractor_for_supplier(invoice.supplier.name)
            _raw_text, extracted_data = extractor.convert_pdf_with_fitz(
                invoice.invoice_file.path
            )

            # Update invoice
            invoice.invoice_data = extracted_data
            invoice.total_net_amount = extracted_data.get('invoice_metadata', {}).get(
                'total_net_amount', 0
            )
            invoice.total_vat_amount = extracted_data.get('invoice_metadata', {}).get(
                'total_vat_amount', 0
            )
            invoice.invoice_total = extracted_data.get('invoice_metadata', {}).get(
                'invoice_total', 0
            )

            # Create items
            products_count = len(extracted_data.get('products', []))
            self.stdout.write(f'  Found {products_count} products')

            for product in extracted_data.get('products', []):
                InvoiceItem.objects.get_or_create(
                    invoice=invoice,
                    seller_sku=product.get('seller_sku', ''),
                    description=product.get('description', ''),
                    defaults={
                        'quantity': product.get('quantity', 0),
                        'unit_price': product.get('unit_price', 0),
                        'total_price': product.get('total_price', 0),
                        'tax_rate': product.get('tax', 20),
                    },
                )

            invoice.status = 'COMPLETED'
            invoice.processed_at = timezone.now()
            invoice.save()

            InvoiceProcessingLog.objects.create(
                invoice=invoice,
                action='EXTRACT',
                message=f'Extracted {products_count} items',
            )

            self.stdout.write(self.style.SUCCESS('  ✓ Extraction completed'))

            # Auto match if requested
            if options['auto_match']:
                self.auto_match_items(invoice)

            # Create stock if requested
            if options['create_stock']:
                self.create_stock_entries(invoice)

        except Exception as e:
            invoice.status = 'FAILED'
            invoice.error_message = str(e)
            invoice.save()

            InvoiceProcessingLog.objects.create(
                invoice=invoice, action='ERROR', message=f'Processing failed: {e!s}'
            )

            self.stdout.write(self.style.ERROR(f'  ✗ Error: {e!s}'))

    def auto_match_items(self, invoice: Invoice):
        """Auto match unmatched items."""
        self.stdout.write('  Auto-matching parts...')
        matched = 0

        for item in invoice.items.filter(part__isnull=True):
            part = self._find_matching_part(item, invoice.supplier)
            if part:
                item.match_part(part)
                matched += 1

        self.stdout.write(f'  ✓ Matched {matched} items')

    def _find_matching_part(self, item: InvoiceItem, supplier):
        """Find matching part for invoice item.

        Strategy:
        1. Try to match by SKU if available
        2. Try to match by description (with supplier preference)
        3. Try fuzzy match by keywords
        """
        from company.models import SupplierPart

        # Strategy 1: Match by SKU
        if item.seller_sku:
            try:
                return Part.objects.get(SKU=item.seller_sku)
            except Part.DoesNotExist:
                pass

        # Strategy 2: Exact description match
        try:
            return Part.objects.get(name__iexact=item.description)
        except Part.MultipleObjectsReturned:
            # Multiple matches - try to filter by supplier
            parts = Part.objects.filter(name__iexact=item.description)
            supplier_part = SupplierPart.objects.filter(
                part__in=parts, supplier=supplier
            ).first()
            if supplier_part:
                return supplier_part.part
            return parts.first()
        except Part.DoesNotExist:
            pass

        # Strategy 3: Case-insensitive substring match
        query = Part.objects.filter(name__icontains=item.description)
        if query.exists():
            if query.count() == 1:
                return query.first()
            # Multiple matches - try to filter by supplier
            supplier_parts = SupplierPart.objects.filter(
                part__in=query, supplier=supplier
            )
            if supplier_parts.exists():
                return supplier_parts.first().part
            return query.first()

        # Strategy 4: Match by keywords
        keywords = item.description.split()
        if not keywords:
            return None

        common_words = {
            'the',
            'a',
            'an',
            'and',
            'or',
            'for',
            'with',
            'in',
            'on',
            'at',
            'by',
        }
        keywords = [kw for kw in keywords[:5] if kw.lower() not in common_words][:3]

        if not keywords:
            return None

        query = Part.objects.all()
        for keyword in keywords:
            query = query.filter(name__icontains=keyword)

        if query.exists():
            if query.count() == 1:
                return query.first()
            # Multiple matches - prefer supplier
            supplier_parts = SupplierPart.objects.filter(
                part__in=query, supplier=supplier
            )
            if supplier_parts.exists():
                return supplier_parts.first().part
            return query.first()

        return None

    def create_stock_entries(self, invoice: Invoice):
        """Create stock entries."""
        self.stdout.write('  Creating stock entries...')

        missing = invoice.get_missing_parts()
        if missing:
            self.stdout.write(
                self.style.WARNING(
                    f'  ⚠ {len(missing)} items still unmatched, skipping stock creation'
                )
            )
            return

        created = 0
        for item in invoice.items.filter(matched=True):
            try:
                item.create_stock_entry()
                created += 1
                InvoiceProcessingLog.objects.create(
                    invoice=invoice,
                    action='STOCK_CREATE',
                    message=f'Created stock: {item.part.name} x{item.quantity}',
                )
            except Exception as e:
                InvoiceProcessingLog.objects.create(
                    invoice=invoice, action='STOCK_ERROR', message=f'Stock error: {e!s}'
                )

        self.stdout.write(self.style.SUCCESS(f'  ✓ Created {created} stock entries'))
