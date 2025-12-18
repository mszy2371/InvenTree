"""Django Admin configuration for Amazon Sales module."""

from django.contrib import admin, messages
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from .models import AmazonSalesReport, AmazonSalesReportLine, AmazonSalesReportStatus


class AmazonSalesReportLineInline(admin.TabularInline):
    """Inline admin for report lines."""

    model = AmazonSalesReportLine
    extra = 1
    readonly_fields = ['match_status_display', 'sales_order']
    fields = [
        'row_number',
        'asin',
        'amazon_order_id',
        'quantity',
        'product_amount',
        'match_status_display',
        'matched_part',
        'sales_order',
    ]
    raw_id_fields = ['matched_part']
    can_delete = True

    def match_status_display(self, obj):
        """Display match status with color."""
        colors = {
            AmazonSalesReportLine.MATCH_PENDING: 'gray',
            AmazonSalesReportLine.MATCH_AUTO: 'green',
            AmazonSalesReportLine.MATCH_MANUAL: 'blue',
            AmazonSalesReportLine.MATCH_NOT_FOUND: 'red',
            AmazonSalesReportLine.MATCH_NO_ASIN: 'orange',
        }
        color = colors.get(obj.match_status, 'gray')
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            color,
            obj.get_match_status_display(),
        )

    match_status_display.short_description = _('Match Status')


@admin.register(AmazonSalesReport)
class AmazonSalesReportAdmin(admin.ModelAdmin):
    """Admin for Amazon Sales Reports."""

    list_display = [
        'name',
        'status_display',
        'date_from',
        'date_to',
        'total_lines',
        'matched_lines',
        'unmatched_lines',
        'orders_created',
        'created',
    ]
    list_filter = ['status', 'created']
    search_fields = ['name', 'notes']
    readonly_fields = [
        'status',
        'created',
        'total_lines',
        'matched_lines',
        'unmatched_lines',
        'orders_created',
        'notes',
    ]
    fieldsets = (
        (None, {'fields': ('name', 'csv_file', 'date_from', 'date_to', 'location')}),
        (_('Status'), {'fields': ('status', 'created')}),
        (
            _('Statistics'),
            {
                'fields': (
                    'total_lines',
                    'matched_lines',
                    'unmatched_lines',
                    'orders_created',
                )
            },
        ),
        (_('Processing Log'), {'fields': ('notes',), 'classes': ('collapse',)}),
    )
    inlines = [AmazonSalesReportLineInline]
    actions = [
        'action_parse_csv',
        'action_match_asins',
        'action_check_ready',
        'action_create_sales_orders',
    ]

    def status_display(self, obj):
        """Display status with color."""
        colors = {
            AmazonSalesReportStatus.UPLOADED: 'gray',
            AmazonSalesReportStatus.PARSED: 'blue',
            AmazonSalesReportStatus.MATCHING: 'orange',
            AmazonSalesReportStatus.READY: 'green',
            AmazonSalesReportStatus.PROCESSING: 'purple',
            AmazonSalesReportStatus.COMPLETED: 'darkgreen',
            AmazonSalesReportStatus.ERROR: 'red',
        }
        color = colors.get(obj.status, 'gray')
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            color,
            obj.get_status_display(),
        )

    status_display.short_description = _('Status')

    @admin.action(description=_('1️⃣ Parse CSV file'))
    def action_parse_csv(self, request, queryset):
        """Parse uploaded CSV files."""
        for report in queryset:
            if report.status not in [
                AmazonSalesReportStatus.UPLOADED,
                AmazonSalesReportStatus.ERROR,
            ]:
                self.message_user(
                    request,
                    f'{report}: Already parsed. Status: {report.get_status_display()}',
                    messages.WARNING,
                )
                continue

            lines, error = report.parse_csv()

            if error:
                self.message_user(
                    request, f'{report}: Error parsing CSV - {error}', messages.ERROR
                )
            else:
                self.message_user(
                    request,
                    f'{report}: Parsed {lines} lines successfully',
                    messages.SUCCESS,
                )

    @admin.action(description=_('2️⃣ Match ASINs to Parts'))
    def action_match_asins(self, request, queryset):
        """Match ASINs in report lines to Parts."""
        for report in queryset:
            if report.status < AmazonSalesReportStatus.PARSED:
                self.message_user(
                    request, f'{report}: Must parse CSV first', messages.WARNING
                )
                continue

            matched, unmatched = report.match_asins()

            self.message_user(
                request,
                f'{report}: Matched {matched}, Unmatched {unmatched}',
                messages.SUCCESS if unmatched == 0 else messages.WARNING,
            )

    @admin.action(description=_('3️⃣ Check if ready (all matched)'))
    def action_check_ready(self, request, queryset):
        """Check if all lines are matched and ready to process."""
        for report in queryset:
            if report.check_ready():
                self.message_user(
                    request,
                    f'{report}: ✅ Ready to create Sales Orders!',
                    messages.SUCCESS,
                )
            else:
                unmatched = report.lines.filter(
                    match_status__in=[
                        AmazonSalesReportLine.MATCH_NOT_FOUND,
                        AmazonSalesReportLine.MATCH_NO_ASIN,
                    ]
                ).count()
                self.message_user(
                    request,
                    f'{report}: ❌ Still {unmatched} unmatched lines. '
                    f'Please match them manually before proceeding.',
                    messages.WARNING,
                )

    @admin.action(description=_('4️⃣ Create Sales Orders'))
    def action_create_sales_orders(self, request, queryset):
        """Create Sales Orders from matched lines."""
        for report in queryset:
            if report.status < AmazonSalesReportStatus.MATCHING:
                self.message_user(
                    request, f'{report}: Must match ASINs first', messages.WARNING
                )
                continue

            # Check for unmatched
            unmatched = report.lines.filter(
                match_status__in=[
                    AmazonSalesReportLine.MATCH_NOT_FOUND,
                    AmazonSalesReportLine.MATCH_NO_ASIN,
                ]
            ).count()

            if unmatched > 0:
                self.message_user(
                    request,
                    f'{report}: {unmatched} lines still unmatched! '
                    f'Proceeding will skip those lines.',
                    messages.WARNING,
                )

            orders, lines, errors = report.create_sales_orders()

            if errors:
                self.message_user(
                    request,
                    f'{report}: Created {orders} orders, {len(errors)} errors',
                    messages.WARNING,
                )
            else:
                self.message_user(
                    request,
                    f'{report}: ✅ Created {orders} Sales Orders ({lines} line items)',
                    messages.SUCCESS,
                )


@admin.register(AmazonSalesReportLine)
class AmazonSalesReportLineAdmin(admin.ModelAdmin):
    """Admin for individual report lines - for manual matching."""

    list_display = [
        'report',
        'row_number',
        'asin',
        'amazon_order_id',
        'quantity',
        'product_amount',
        'match_status',
        'matched_part',
        'sales_order',
    ]
    list_filter = ['report', 'match_status']
    search_fields = ['asin', 'amazon_order_id', 'matched_part__name']
    list_editable = ['matched_part']
    raw_id_fields = ['matched_part', 'sales_order']
    actions = ['action_assign_asin_to_part', 'action_mark_manual_match']

    def get_queryset(self, request):
        """Optimize queryset with select_related."""
        return (
            super()
            .get_queryset(request)
            .select_related('report', 'matched_part', 'sales_order')
        )

    @admin.action(description=_('Assign ASIN to matched Part'))
    def action_assign_asin_to_part(self, request, queryset):
        """Assign ASIN as Part Parameter to matched parts."""
        assigned = 0
        for line in queryset:
            if line.matched_part and line.asin:
                if line.assign_asin_to_part():
                    assigned += 1

        self.message_user(
            request, f'Assigned ASIN to {assigned} parts', messages.SUCCESS
        )

    @admin.action(description=_('Mark as manually matched'))
    def action_mark_manual_match(self, request, queryset):
        """Mark selected lines as manually matched."""
        updated = 0
        for line in queryset:
            if line.matched_part:
                line.match_status = AmazonSalesReportLine.MATCH_MANUAL
                line.save()
                updated += 1

        self.message_user(
            request, f'Marked {updated} lines as manually matched', messages.SUCCESS
        )
