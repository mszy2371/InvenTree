"""Models for Amazon Sales Report importer."""

import csv
from decimal import Decimal, InvalidOperation
from io import TextIOWrapper

from django.db import models
from django.utils.translation import gettext_lazy as _

from company.models import Company
from order.models import SalesOrder, SalesOrderLineItem
from order.status_codes import SalesOrderStatus
from part.models import Part, PartParameter, PartParameterTemplate


class AmazonSalesReportStatus:
    """Status codes for Amazon Sales Report."""

    UPLOADED = 10
    PARSED = 20
    MATCHING = 30
    READY = 40
    PROCESSING = 50
    COMPLETED = 60
    ERROR = 90

    CHOICES = [
        (UPLOADED, _('Uploaded')),
        (PARSED, _('Parsed')),
        (MATCHING, _('Matching ASINs')),
        (READY, _('Ready to Process')),
        (PROCESSING, _('Processing')),
        (COMPLETED, _('Completed')),
        (ERROR, _('Error')),
    ]


def amazon_report_upload_path(instance, filename):
    """Generate upload path for Amazon sales report."""
    return f'amazon_reports/{instance.name or "report"}_{filename}'


class AmazonSalesReport(models.Model):
    """Model representing an uploaded Amazon Sales Report CSV."""

    class Meta:
        """Model meta options."""

        verbose_name = _('Amazon Sales Report')
        verbose_name_plural = _('Amazon Sales Reports')
        ordering = ['-created']

    name = models.CharField(
        max_length=100,
        blank=True,
        verbose_name=_('Name'),
        help_text=_('Optional name for this report (e.g., "July 2024")'),
    )

    csv_file = models.FileField(
        upload_to=amazon_report_upload_path,
        verbose_name=_('CSV File'),
        help_text=_('Amazon sales report CSV file'),
    )

    date_from = models.DateField(
        null=True,
        blank=True,
        verbose_name=_('Date From'),
        help_text=_('Start date of the report period'),
    )

    date_to = models.DateField(
        null=True,
        blank=True,
        verbose_name=_('Date To'),
        help_text=_('End date of the report period'),
    )

    status = models.IntegerField(
        choices=AmazonSalesReportStatus.CHOICES,
        default=AmazonSalesReportStatus.UPLOADED,
        verbose_name=_('Status'),
    )

    created = models.DateTimeField(auto_now_add=True, verbose_name=_('Created'))

    notes = models.TextField(
        blank=True, verbose_name=_('Notes'), help_text=_('Processing notes and logs')
    )

    # Location for stock allocation
    location = models.ForeignKey(
        'stock.StockLocation',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_('Stock Location'),
        help_text=_('Location to allocate stock from when creating sales orders'),
    )

    # Statistics
    total_lines = models.IntegerField(default=0, verbose_name=_('Total Lines'))
    matched_lines = models.IntegerField(default=0, verbose_name=_('Matched Lines'))
    unmatched_lines = models.IntegerField(default=0, verbose_name=_('Unmatched Lines'))
    orders_created = models.IntegerField(default=0, verbose_name=_('Orders Created'))

    def __str__(self):
        """String representation."""
        if self.name:
            return f'{self.name} ({self.get_status_display()})'
        return f'Report {self.pk} ({self.get_status_display()})'

    def log(self, message: str):
        """Add a log message to notes."""
        self.notes += f'{message}\n'
        self.save(update_fields=['notes'])

    def parse_csv(self) -> tuple[int, str]:
        """Parse the uploaded CSV file and create AmazonSalesReportLine entries.

        Returns:
            Tuple of (lines_created, error_message)
        """
        # Clear existing lines and reset statistics
        self.lines.all().delete()
        self.notes = ''
        self.total_lines = 0
        self.matched_lines = 0
        self.unmatched_lines = 0
        self.orders_created = 0

        try:
            self.csv_file.seek(0)
            # Handle BOM encoding
            wrapper = TextIOWrapper(self.csv_file, encoding='utf-8-sig')
            reader = csv.DictReader(wrapper)

            lines_created = 0
            for row_num, row in enumerate(reader, start=2):
                try:
                    # Parse quantity
                    quantity = int(row.get('Quantity', 1))

                    # Parse amounts
                    product_amount = self._parse_decimal(row.get('Product Amount', '0'))
                    shipping_amount = self._parse_decimal(
                        row.get('Shipping Amount', '0')
                    )

                    AmazonSalesReportLine.objects.create(
                        report=self,
                        row_number=row_num,
                        shipment_date=row.get('Customer Shipment Date', ''),
                        merchant_sku=row.get('Merchant SKU', ''),
                        fnsku=row.get('FNSKU', ''),
                        asin=row.get('ASIN', ''),
                        fulfillment_center=row.get('FC', ''),
                        quantity=quantity,
                        amazon_order_id=row.get('Amazon Order Id', ''),
                        currency=row.get('Currency', 'GBP'),
                        product_amount=product_amount,
                        shipping_amount=shipping_amount,
                        city=row.get('Shipment To City', ''),
                        state=row.get('Shipment To State', ''),
                        postal_code=row.get('Shipment To Postal Code', ''),
                    )
                    lines_created += 1
                except Exception as e:
                    self.log(f'Row {row_num}: Error parsing - {e}')

            self.total_lines = lines_created
            self.status = AmazonSalesReportStatus.PARSED
            
            # Extract date_from and date_to from shipment dates
            if lines_created > 0:
                dates = []
                for line in self.lines.all():
                    if line.shipment_date:
                        try:
                            # Parse ISO format datetime (e.g., "2024-10-31T22:01:54+00:00")
                            from dateutil import parser as date_parser
                            parsed_dt = date_parser.isoparse(line.shipment_date)
                            dates.append(parsed_dt.date())
                        except (ValueError, TypeError):
                            pass
                
                if dates:
                    self.date_from = min(dates)
                    self.date_to = max(dates)
            
            # Calculate matched and unmatched lines
            self.matched_lines = self.lines.filter(matched_part__isnull=False).count()
            self.unmatched_lines = self.lines.filter(matched_part__isnull=True).count()
            
            self.save()

            return lines_created, ''

        except Exception as e:
            self.status = AmazonSalesReportStatus.ERROR
            self.log(f'Error parsing CSV: {e}')
            self.save()
            return 0, str(e)

    def _parse_decimal(self, value: str) -> Decimal:
        """Parse a decimal value from string."""
        if not value:
            return Decimal('0')
        try:
            return Decimal(value.replace(',', '.'))
        except InvalidOperation:
            return Decimal('0')

    def match_asins(self) -> tuple[int, int]:
        """Match ASINs to Parts using PartParameter.

        Returns:
            Tuple of (matched_count, unmatched_count)
        """
        # Get ASIN template
        try:
            asin_template = PartParameterTemplate.objects.get(name='ASIN')
        except PartParameterTemplate.DoesNotExist:
            self.log('Error: ASIN parameter template not found!')
            return 0, self.lines.count()

        # Reset match statuses before matching
        for line in self.lines.all():
            line.matched_part = None
            line.match_status = AmazonSalesReportLine.MATCH_PENDING
            line.save()

        matched = 0
        unmatched = 0

        for line in self.lines.all():
            if not line.asin:
                line.match_status = AmazonSalesReportLine.MATCH_NO_ASIN
                line.save()
                unmatched += 1
                continue

            # Find Part by ASIN
            param = PartParameter.objects.filter(
                template=asin_template, data=line.asin
            ).first()

            if param:
                line.matched_part = param.part
                line.match_status = AmazonSalesReportLine.MATCH_AUTO
                matched += 1
            else:
                line.match_status = AmazonSalesReportLine.MATCH_NOT_FOUND
                unmatched += 1

            line.save()

        self.matched_lines = matched
        self.unmatched_lines = unmatched
        self.status = AmazonSalesReportStatus.MATCHING
        self.save()

        return matched, unmatched

    def check_ready(self) -> bool:
        """Check if all lines are matched and ready to process."""
        unmatched = self.lines.filter(
            match_status__in=[
                AmazonSalesReportLine.MATCH_NOT_FOUND,
                AmazonSalesReportLine.MATCH_NO_ASIN,
            ]
        ).count()

        if unmatched == 0:
            self.status = AmazonSalesReportStatus.READY
            self.save()
            return True
        return False

    def create_sales_orders(self) -> tuple[int, int, list]:
        """Create a single Sales Order from all matched lines in this report.

        Returns:
            Tuple of (orders_created, lines_processed, errors)
        """
        from django.db import transaction

        from stock.models import StockItem

        errors = []
        lines_processed = 0

        self.status = AmazonSalesReportStatus.PROCESSING
        self.save()

        try:
            with transaction.atomic():
                # Ensure customer exists
                customer, _ = Company.objects.get_or_create(
                    name='Amazon UK',
                    defaults={
                        'is_customer': True,
                        'description': 'Amazon UK Marketplace',
                    },
                )

                # Create a unique reference for this report (always create new SO)
                # Use timestamp to ensure uniqueness even for identical reports
                from django.utils import timezone
                timestamp = timezone.now().strftime('%Y%m%d%H%M%S')
                report_reference = f'AMZ-{self.name or self.pk}-{timestamp}'

                # Create single Sales Order (start as PENDING)
                so = SalesOrder.objects.create(
                    customer=customer,
                    customer_reference=report_reference,
                    description=f'Amazon Sales Report: {self.name or self.pk}',
                    target_date=timezone.now().date(),
                    status=SalesOrderStatus.PENDING.value,
                )
                self.log(f'Created Sales Order: {so.reference}')

                # Create default shipment
                from order.models import SalesOrderAllocation, SalesOrderShipment
                from dateutil import parser as date_parser

                # Process each matched line
                for line in self.lines.filter(matched_part__isnull=False):
                    try:
                        # Parse shipment date from Amazon report
                        line_date = timezone.now().date()
                        if line.shipment_date:
                            try:
                                parsed_dt = date_parser.isoparse(line.shipment_date)
                                line_date = parsed_dt.date()
                            except (ValueError, TypeError):
                                pass

                        # Create/get shipment for this specific date
                        shipment, _ = SalesOrderShipment.objects.get_or_create(
                            order=so,
                            reference=line_date.isoformat(),  # Use date as reference
                            defaults={'shipment_date': line_date}
                        )

                        part = line.matched_part

                        # Ensure part is salable
                        if not part.salable:
                            part.salable = True
                            part.save()

                        # Calculate unit price
                        unit_price = line.product_amount
                        if line.quantity > 1:
                            unit_price = line.product_amount / line.quantity

                        # Create line item
                        so_line = SalesOrderLineItem.objects.create(
                            order=so,
                            part=part,
                            quantity=line.quantity,
                            sale_price=unit_price,
                            shipped=line.quantity,
                            target_date=line_date,
                        )

                        # Find stock item from required location
                        if not self.location:
                            raise ValueError('Stock Location is required to allocate stock items')
                        
                        stock_item = StockItem.objects.filter(
                            part=part,
                            location=self.location,
                            quantity__gte=line.quantity
                        ).first()

                        if not stock_item:
                            raise ValueError(
                                f'{part.name}: insufficient stock in {self.location.name}. '
                                f'Required: {line.quantity}'
                            )

                        # Create allocation
                        SalesOrderAllocation.objects.create(
                            line=so_line,
                            shipment=shipment,
                            item=stock_item,
                            quantity=line.quantity,
                        )

                        # Reduce stock
                        stock_item.quantity -= line.quantity
                        stock_item.save()

                        line.sales_order = so
                        line.save()
                        lines_processed += 1

                    except Exception as e:
                        errors.append(f'Line {line.row_number}: {e}')
                        self.log(f'Error processing line {line.row_number}: {e}')

                # Mark SO as shipped
                so.status = SalesOrderStatus.SHIPPED.value
                so.save()

                self.orders_created += 1
                self.log(f'✅ Completed: {lines_processed} lines processed')

        except Exception as e:
            errors.append(str(e))
            self.log(f'Error: {e}')
            self.status = AmazonSalesReportStatus.ERROR
            self.save()
            return 0, 0, errors

        self.status = AmazonSalesReportStatus.COMPLETED
        self.save()

        return 1, lines_processed, errors


class AmazonSalesReportLine(models.Model):
    """Model representing a single line from Amazon Sales Report."""

    MATCH_PENDING = 0
    MATCH_AUTO = 1
    MATCH_MANUAL = 2
    MATCH_NOT_FOUND = 3
    MATCH_NO_ASIN = 4

    MATCH_STATUS_CHOICES = [
        (MATCH_PENDING, _('Pending')),
        (MATCH_AUTO, _('Auto-matched')),
        (MATCH_MANUAL, _('Manually matched')),
        (MATCH_NOT_FOUND, _('Not Found')),
        (MATCH_NO_ASIN, _('No ASIN')),
    ]

    class Meta:
        """Model meta options."""

        verbose_name = _('Report Line')
        verbose_name_plural = _('Report Lines')
        ordering = ['row_number']

    report = models.ForeignKey(
        AmazonSalesReport,
        on_delete=models.CASCADE,
        related_name='lines',
        verbose_name=_('Report'),
    )

    row_number = models.IntegerField(
        verbose_name=_('Row'), help_text=_('Row number in CSV')
    )

    # Amazon data fields
    shipment_date = models.CharField(
        max_length=50, blank=True, verbose_name=_('Shipment Date')
    )

    merchant_sku = models.CharField(
        max_length=50, blank=True, verbose_name=_('Merchant SKU')
    )

    fnsku = models.CharField(max_length=20, blank=True, verbose_name=_('FNSKU'))

    asin = models.CharField(
        max_length=20, blank=True, verbose_name=_('ASIN'), db_index=True
    )

    fulfillment_center = models.CharField(
        max_length=10, blank=True, verbose_name=_('FC')
    )

    quantity = models.IntegerField(default=1, verbose_name=_('Quantity'))

    amazon_order_id = models.CharField(
        max_length=30, blank=True, verbose_name=_('Amazon Order Id'), db_index=True
    )

    currency = models.CharField(max_length=3, default='GBP', verbose_name=_('Currency'))

    product_amount = models.DecimalField(
        max_digits=10, decimal_places=2, default=0, verbose_name=_('Product Amount')
    )

    shipping_amount = models.DecimalField(
        max_digits=10, decimal_places=2, default=0, verbose_name=_('Shipping Amount')
    )

    city = models.CharField(max_length=100, blank=True, verbose_name=_('City'))

    state = models.CharField(max_length=50, blank=True, verbose_name=_('State'))

    postal_code = models.CharField(
        max_length=20, blank=True, verbose_name=_('Postal Code')
    )

    # Matching fields
    matched_part = models.ForeignKey(
        Part,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='amazon_report_lines',
        verbose_name=_('Matched Part'),
    )

    match_status = models.IntegerField(
        choices=MATCH_STATUS_CHOICES,
        default=MATCH_PENDING,
        verbose_name=_('Match Status'),
    )

    # Created Sales Order reference
    sales_order = models.ForeignKey(
        SalesOrder,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='amazon_report_lines',
        verbose_name=_('Sales Order'),
    )

    def __str__(self):
        """String representation."""
        return f'Row {self.row_number}: {self.asin} x{self.quantity}'

    def assign_asin_to_part(self):
        """Assign this line's ASIN to the matched Part as a parameter."""
        if not self.matched_part or not self.asin:
            return False

        template, _ = PartParameterTemplate.objects.get_or_create(
            name='ASIN',
            defaults={'description': 'Amazon Standard Identification Number'},
        )

        param, created = PartParameter.objects.get_or_create(
            part=self.matched_part, template=template, defaults={'data': self.asin}
        )

        if not created and param.data != self.asin:
            param.data = self.asin
            param.save()

        return True
