"""Django admin configuration for invoice management."""

import json
import re
from datetime import datetime
from decimal import Decimal

from django.contrib import admin, messages
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.utils.html import format_html

from company.models import SupplierPart
from order.models import PurchaseOrder, PurchaseOrderLineItem
from part.models import Part
from stock.models import StockItem, StockLocation

from .models import Invoice, InvoiceItem, InvoiceProcessingLog


class InvoiceItemInline(admin.TabularInline):
    """Inline display of invoice items."""

    model = InvoiceItem
    extra = 1  # Allow adding new items
    readonly_fields = ('part_actions',)
    fields = (
        'part',
        'part_actions',
        'description',
        'seller_sku',
        'quantity',
        'unit_price',
        'total_price',
        'tax_rate',
        'matched',
        'notes',
    )
    raw_id_fields = (
        'part',
    )  # Use popup instead of dropdown for Part selection (6000+ parts!)
    can_delete = True  # Allow deleting items

    def part_actions(self, obj):
        """Show action buttons for unmatched items."""
        if obj.pk and not obj.matched:
            # Link to create new Part with prefilled data from this item
            create_url = reverse(
                'admin:invoice_manager_create_part_from_item', args=[obj.pk]
            )

            return format_html(
                '<a href="{}" target="_blank" class="button" '
                'style="background: #28a745; color: white; padding: 3px 8px; '
                'border-radius: 3px; text-decoration: none; font-size: 11px;">'
                '+ Create Part</a>',
                create_url,
            )
        elif obj.matched:
            return format_html('<span style="color: #28a745;">✓ Matched</span>')
        return '-'

    part_actions.short_description = 'Actions'


class InvoiceProcessingLogInline(admin.TabularInline):
    """Inline display of processing logs."""

    model = InvoiceProcessingLog
    extra = 0
    readonly_fields = ('action', 'message', 'created_at')
    can_delete = False


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    """Admin interface for invoices."""

    list_display = (
        'invoice_number',
        'supplier_name',
        'invoice_date',
        'status_badge',
        'total_items',
        'missing_parts_count',
        'purchase_order_link',
        'match_parts_link',
        'created_at',
    )
    list_filter = ('status', 'supplier', 'created_at', 'invoice_date')
    search_fields = ('invoice_number', 'supplier__name')
    readonly_fields = (
        'invoice_data_display',
        'created_at',
        'updated_at',
        'processed_at',
        'total_net_amount',
        'total_vat_amount',
        'invoice_total',
        'purchase_order',
    )

    fieldsets = (
        (
            'Invoice Information',
            {'fields': ('invoice_number', 'invoice_date', 'supplier', 'uploaded_by')},
        ),
        ('Document', {'fields': ('invoice_file',)}),
        (
            'Financial Summary',
            {'fields': ('total_net_amount', 'total_vat_amount', 'invoice_total')},
        ),
        (
            'Processing',
            {'fields': ('status', 'processing_log', 'error_message', 'processed_at')},
        ),
        (
            'Extracted Data',
            {'fields': ('invoice_data_display',), 'classes': ('collapse',)},
        ),
        (
            'Timestamps',
            {'fields': ('created_at', 'updated_at'), 'classes': ('collapse',)},
        ),
    )

    inlines = [InvoiceItemInline, InvoiceProcessingLogInline]
    actions = [
        'extract_invoice_data',
        'auto_match_parts',
        'create_purchase_order',
        'reset_invoice',
    ]

    def get_urls(self):
        """Add custom URLs for matching view."""
        urls = super().get_urls()
        custom_urls = [
            path(
                '<int:invoice_id>/match/',
                self.admin_site.admin_view(self.match_parts_view),
                name='invoice_manager_invoice_match',
            ),
            path(
                'search-parts/',
                self.admin_site.admin_view(self.search_parts_api),
                name='invoice_manager_search_parts',
            ),
            path(
                'create-part/',
                self.admin_site.admin_view(self.create_part_api),
                name='invoice_manager_create_part',
            ),
            path(
                'item/<int:item_id>/create-part/',
                self.admin_site.admin_view(self.create_part_from_item_view),
                name='invoice_manager_create_part_from_item',
            ),
        ]
        return custom_urls + urls

    def create_part_from_item_view(self, request, item_id):
        """Create Part and SupplierPart from invoice item, then assign to item."""
        from part.models import Part

        item = get_object_or_404(InvoiceItem, pk=item_id)
        invoice = item.invoice

        # Parse description to extract useful data
        description = item.description or ''

        # Try to extract clean product name (before technical details)
        # Example: "W7 Tea Tree Concealer - Light/Medium (3pcs) (1581) (£0.81/each) A/89 Line Weight: ..."
        name = description

        # Remove "Line Weight:" and everything after
        if 'Line Weight:' in name:
            name = name.split('Line Weight:')[0].strip()

        # Remove "Country of Origin:" and everything after
        if 'Country of Origin:' in name:
            name = name.split('Country of Origin:')[0].strip()

        # Remove "HS Code:" and everything after
        if 'HS Code:' in name:
            name = name.split('HS Code:')[0].strip()

        # Remove location codes like "A/89", "B/25" at the end
        name = re.sub(r'\s+[A-Z]+/\d+\s*$', '', name)

        # Remove price info like "(£0.81/each)" or "(€0.81/each)"
        name = re.sub(r'\([£€\?][\d.]+/each\)', '', name)

        # Clean up extra spaces
        name = ' '.join(name.split())

        # Limit name length
        if len(name) > 100:
            name = name[:100]

        if request.method == 'POST':
            # Get values from form (user-editable)
            part_name = request.POST.get('part_name', name).strip()
            supplier_sku = request.POST.get('supplier_sku', '').strip()
            unit_price = request.POST.get('unit_price', '')

            # Validate part name
            if not part_name:
                part_name = name

            # Parse unit price
            try:
                price_value = float(unit_price) if unit_price else None
            except ValueError:
                price_value = None

            # Create the Part
            part = Part.objects.create(
                name=part_name,
                description=description[:250] if description else '',
                keywords=supplier_sku or '',
                purchaseable=True,
                salable=True,
                active=True,
            )

            # Create SupplierPart if we have supplier
            supplier_part = None
            if invoice.supplier:
                supplier_part = SupplierPart.objects.create(
                    part=part,
                    supplier=invoice.supplier,
                    SKU=supplier_sku or '',
                    description=part_name,
                )

                # Set price if provided
                if price_value and supplier_part:
                    from company.models import SupplierPriceBreak

                    SupplierPriceBreak.objects.create(
                        part=supplier_part, quantity=1, price=price_value
                    )

            # Assign the Part to the InvoiceItem
            item.part = part
            item.matched = True
            item.save()

            # Log the action
            InvoiceProcessingLog.objects.create(
                invoice=invoice,
                action='MANUAL_MATCH',
                message=f"Created Part '{part_name}' and assigned to '{item.description[:50]}...'",
            )

            sku_msg = f" with SKU '{supplier_sku}'" if supplier_sku else ''
            messages.success(
                request,
                f"Created Part '{part_name}'{sku_msg} and assigned to invoice item.",
            )

            # Redirect back to invoice
            return redirect(
                reverse('admin:invoice_manager_invoice_change', args=[invoice.pk])
            )

        # GET request - show confirmation form
        context = {
            'title': 'Create Part from Invoice Item',
            'item': item,
            'invoice': invoice,
            'parsed_name': name,
            'sku': item.seller_sku,
            'supplier': invoice.supplier,
            'opts': self.model._meta,
            'has_change_permission': True,
        }
        return render(
            request, 'admin/invoice_manager/create_part_confirm.html', context
        )

    def match_parts_view(self, request, invoice_id):
        """Interactive view for matching invoice items to parts."""
        invoice = get_object_or_404(Invoice, pk=invoice_id)
        items = list(invoice.items.all().select_related('part'))

        # Add suggestions for unmatched items
        for item in items:
            if not item.matched:
                item.suggestions = self._get_part_suggestions(item, invoice.supplier)

        if request.method == 'POST':
            action = request.POST.get('action')
            matched_count = 0

            # Process each item's part assignment
            for item in items:
                part_id = request.POST.get(f'part_{item.pk}')
                if part_id:
                    try:
                        part = Part.objects.get(pk=int(part_id))
                        if item.part != part:
                            item.match_part(part)
                            matched_count += 1
                            InvoiceProcessingLog.objects.create(
                                invoice=invoice,
                                action='MANUAL_MATCH',
                                message=f"Manually matched '{item.description[:50]}' to {part.name}",
                            )
                    except (Part.DoesNotExist, ValueError):
                        pass
                elif item.part:
                    # Clear existing match if field is empty
                    item.part = None
                    item.matched = False
                    item.save()

            if action == 'save_and_stock':
                # Create stock entries for matched items
                stock_created = self._create_stock_for_invoice(invoice, request)
                messages.success(
                    request,
                    f'Saved {matched_count} matches and created {stock_created} stock entries.',
                )
            else:
                messages.success(request, f'Saved {matched_count} part matches.')

            return redirect(
                reverse('admin:invoice_manager_invoice_match', args=[invoice_id])
            )

        # Get categories for the create part modal
        from part.models import PartCategory

        categories = PartCategory.objects.all().order_by('pathstring')

        context = {
            **self.admin_site.each_context(request),
            'invoice': invoice,
            'items': items,
            'total_items': len(items),
            'matched_count': sum(1 for i in items if i.matched),
            'unmatched_count': sum(1 for i in items if not i.matched),
            'categories': categories,
            'title': f'Match Parts - Invoice {invoice.invoice_number}',
        }
        return render(request, 'admin/invoice_manager/match_parts.html', context)

    def search_parts_api(self, request):
        """API endpoint for part search."""
        query = request.GET.get('q', '').strip()
        supplier_id = request.GET.get('supplier', '')

        if len(query) < 2:
            return JsonResponse({'parts': []})

        # Multi-strategy search
        parts = self._search_parts_flexible(query, supplier_id)

        return JsonResponse({
            'parts': [
                {
                    'id': p.pk,
                    'name': p.name,
                    'IPN': p.IPN or '',
                    'description': p.description or '',
                }
                for p in parts[:20]
            ]
        })

    def create_part_api(self, request):
        """API endpoint to create a new Part from invoice matching view."""
        import json

        from company.models import Company
        from part.models import PartCategory

        if request.method != 'POST':
            return JsonResponse({'success': False, 'error': 'POST required'})

        try:
            data = json.loads(request.body)

            # Validate required fields
            name = data.get('name', '').strip()
            category_id = data.get('category')

            if not name:
                return JsonResponse({
                    'success': False,
                    'error': 'Part name is required',
                })
            if not category_id:
                return JsonResponse({'success': False, 'error': 'Category is required'})

            # Get category
            try:
                category = PartCategory.objects.get(pk=category_id)
            except PartCategory.DoesNotExist:
                return JsonResponse({'success': False, 'error': 'Invalid category'})

            # Create the Part
            part = Part.objects.create(
                name=name,
                description=data.get('description', ''),
                category=category,
                purchaseable=True,
                salable=True,
            )

            # Create SupplierPart if supplier provided
            supplier_id = data.get('supplier_id')
            sku = data.get('sku', '').strip()
            price = data.get('price')

            if supplier_id:
                try:
                    supplier = Company.objects.get(pk=supplier_id)
                    supplier_part = SupplierPart.objects.create(
                        part=part, supplier=supplier, SKU=sku or f'AUTO-{part.pk}'
                    )

                    # Add price if provided
                    if price:
                        supplier_part.update_pricing()
                except Company.DoesNotExist:
                    pass

            return JsonResponse({
                'success': True,
                'part_id': part.pk,
                'part_name': part.name,
            })

        except json.JSONDecodeError:
            return JsonResponse({'success': False, 'error': 'Invalid JSON'})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})

    def _search_parts_flexible(self, query, supplier_id=None):
        """Flexible part search - matches by various strategies."""
        # Clean query
        clean_query = re.sub(r'[^\w\s]', ' ', query.lower())
        words = [w for w in clean_query.split() if len(w) >= 2]

        # Strategy 1: Exact match
        parts = Part.objects.filter(name__iexact=query)
        if parts.exists():
            return parts

        # Strategy 2: Contains full query
        parts = Part.objects.filter(name__icontains=query)
        if parts.exists():
            return parts[:20]

        # Strategy 3: Match all words
        if words:
            q = Part.objects.all()
            for word in words:
                q = q.filter(name__icontains=word)
            if q.exists():
                return q[:20]

        # Strategy 4: Match any significant word
        if words:
            q_objects = Q()
            for word in words:
                if len(word) >= 3:
                    q_objects |= Q(name__icontains=word)
            if q_objects:
                parts = Part.objects.filter(q_objects)
                if parts.exists():
                    # Prefer parts from this supplier
                    if supplier_id:
                        supplier_parts = SupplierPart.objects.filter(
                            supplier_id=supplier_id, part__in=parts
                        ).values_list('part_id', flat=True)
                        if supplier_parts:
                            return Part.objects.filter(pk__in=supplier_parts)[:20]
                    return parts[:20]

        return Part.objects.none()

    def _get_part_suggestions(self, item, supplier, limit=3):
        """Get suggested parts for an invoice item."""
        # Clean description for search
        desc = item.description.lower()
        words = [w for w in re.sub(r'[^\w\s]', ' ', desc).split() if len(w) >= 3]
        stopwords = {'the', 'and', 'for', 'with', 'pack', 'pcs', 'each', 'new'}
        words = [w for w in words if w not in stopwords][:4]

        if not words:
            return []

        # Try matching with decreasing number of words
        for n in range(len(words), 0, -1):
            q = Part.objects.all()
            for word in words[:n]:
                q = q.filter(name__icontains=word)

            if q.exists():
                # Prefer parts from this supplier
                supplier_parts = SupplierPart.objects.filter(
                    supplier=supplier, part__in=q
                ).select_related('part')[:limit]

                if supplier_parts:
                    return [sp.part for sp in supplier_parts]
                return list(q[:limit])

        return []

    def _create_stock_for_invoice(self, invoice, request):
        """Create stock entries for all matched items in invoice."""
        created = 0
        location = StockLocation.objects.first()  # Default location

        for item in invoice.items.filter(matched=True, part__isnull=False):
            try:
                stock = StockItem.objects.create(
                    part=item.part,
                    quantity=item.quantity,
                    location=location,
                    notes=f'From invoice {invoice.invoice_number}',
                )
                created += 1
                InvoiceProcessingLog.objects.create(
                    invoice=invoice,
                    action='STOCK_CREATE',
                    message=f'Created stock: {item.part.name} x{item.quantity} (ID: {stock.pk})',
                )
            except Exception as e:
                InvoiceProcessingLog.objects.create(
                    invoice=invoice,
                    action='STOCK_ERROR',
                    message=f'Failed to create stock for {item.part.name}: {e!s}',
                )

        return created

    def supplier_name(self, obj):
        """Return supplier name."""
        return obj.supplier.name

    supplier_name.short_description = 'Supplier'

    def status_badge(self, obj):
        """Return styled status badge."""
        colors = {
            'PENDING': '#FFA500',
            'PROCESSING': '#4169E1',
            'COMPLETED': '#2E8B57',
            'FAILED': '#DC143C',
            'CANCELLED': '#696969',
        }
        color = colors.get(obj.status, '#808080')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 10px; border-radius: 3px;">{}</span>',
            color,
            obj.get_status_display(),
        )

    status_badge.short_description = 'Status'

    def total_items(self, obj):
        """Return total item count."""
        return obj.items.count()

    total_items.short_description = 'Items'

    def missing_parts_count(self, obj):
        """Return missing parts count badge."""
        missing = obj.get_missing_parts()
        if missing:
            count = len(missing)
            return format_html(
                '<span style="color: red;"><strong>{} missing</strong></span>', count
            )
        return format_html('<span style="color: green;">✓ All matched</span>')

    missing_parts_count.short_description = 'Missing Parts'

    def invoice_data_display(self, obj):
        """Return formatted invoice data."""
        return format_html('<pre>{}</pre>', json.dumps(obj.invoice_data, indent=2))

    invoice_data_display.short_description = 'Raw Invoice Data'

    def match_parts_link(self, obj):
        """Link to match parts view."""
        if obj.items.exists():
            url = reverse('admin:invoice_manager_invoice_match', args=[obj.pk])
            unmatched = obj.items.filter(matched=False).count()
            if unmatched > 0:
                return format_html(
                    '<a href="{}" style="background:#ffc107;color:#333;padding:4px 10px;border-radius:4px;text-decoration:none;">Match Parts ({})</a>',
                    url,
                    unmatched,
                )
            return format_html(
                '<a href="{}" style="background:#28a745;color:white;padding:4px 10px;border-radius:4px;text-decoration:none;">View Matches ✓</a>',
                url,
            )
        return '-'

    match_parts_link.short_description = 'Match'

    def purchase_order_link(self, obj):
        """Return link to Purchase Order if exists."""
        if obj.purchase_order:
            url = reverse(
                'admin:order_purchaseorder_change', args=[obj.purchase_order.pk]
            )
            return format_html(
                '<a href="{}" style="background:#28a745;color:white;padding:4px 10px;border-radius:4px;text-decoration:none;">PO-{}</a>',
                url,
                obj.purchase_order.pk,
            )
        return format_html('<span style="color:#888;padding:4px 10px;">—</span>')

    purchase_order_link.short_description = 'Purchase Order'

    @admin.action(description='Extract invoice data from PDF')
    def extract_invoice_data(self, request, queryset):
        """Extract data from uploaded PDF files."""
        from .extractors import get_extractor_for_supplier

        for invoice in queryset:
            if invoice.status != 'PENDING':
                self.message_user(
                    request,
                    f'Invoice {invoice.invoice_number} is already processed',
                    messages.WARNING,
                )
                continue

            try:
                invoice.status = 'PROCESSING'
                invoice.save()

                # Get extractor for supplier
                extractor = get_extractor_for_supplier(invoice.supplier.name)

                # Extract data from PDF
                _raw_text, extracted_data = extractor.convert_pdf_with_fitz(
                    invoice.invoice_file.path
                )

                # Update invoice with extracted data
                invoice.invoice_data = extracted_data
                invoice.total_net_amount = extracted_data.get(
                    'invoice_metadata', {}
                ).get('total_net_amount', 0)
                invoice.total_vat_amount = extracted_data.get(
                    'invoice_metadata', {}
                ).get('total_vat_amount', 0)
                invoice.invoice_total = extracted_data.get('invoice_metadata', {}).get(
                    'invoice_total', 0
                )

                # Create invoice items
                self._create_invoice_items(invoice, extracted_data)

                invoice.status = 'COMPLETED'
                invoice.processed_at = datetime.now()
                invoice.save()

                InvoiceProcessingLog.objects.create(
                    invoice=invoice,
                    action='EXTRACT',
                    message=f'Successfully extracted {len(extracted_data.get("products", []))} items from invoice',
                )

                self.message_user(
                    request,
                    f'Successfully extracted data from {invoice.invoice_number}',
                    messages.SUCCESS,
                )

            except Exception as e:
                invoice.status = 'FAILED'
                invoice.error_message = str(e)
                invoice.save()

                InvoiceProcessingLog.objects.create(
                    invoice=invoice,
                    action='ERROR',
                    message=f'Failed to extract invoice: {e!s}',
                )

                self.message_user(
                    request,
                    f'Failed to extract {invoice.invoice_number}: {e!s}',
                    messages.ERROR,
                )

    def _create_invoice_items(self, invoice: Invoice, extracted_data: dict):
        """Create invoice items from extracted data."""
        for product in extracted_data.get('products', []):
            _item, _created = InvoiceItem.objects.get_or_create(
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

    @admin.action(description='Auto-match parts (by description)')
    def auto_match_parts(self, request, queryset):
        """Try to automatically match invoice items to parts."""
        matched_count = 0
        unmatched_count = 0

        for invoice in queryset:
            for item in invoice.items.filter(part__isnull=True):
                part = self._find_matching_part(item, invoice.supplier)

                if part:
                    item.match_part(part)
                    matched_count += 1
                    InvoiceProcessingLog.objects.create(
                        invoice=invoice,
                        action='MATCH',
                        message=f"Auto-matched '{item.description[:50]}' to {part.name}",
                    )
                else:
                    unmatched_count += 1

        self.message_user(
            request,
            f'Matched {matched_count} items. {unmatched_count} items require manual matching.',
            messages.SUCCESS if matched_count > 0 else messages.WARNING,
        )

    def _find_matching_part(self, item, supplier):
        """Find matching part for invoice item.

        Strategy:
        1. Try to match by SupplierPart.SKU (most reliable for Shure etc.)
        2. Try to match by description
        3. Try fuzzy match by keywords
        """
        # Strategy 1: Match by SupplierPart SKU (primary method for Shure)
        if item.seller_sku:
            supplier_part = (
                SupplierPart.objects.filter(supplier=supplier, SKU=item.seller_sku)
                .select_related('part')
                .first()
            )
            if supplier_part:
                return supplier_part.part

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

        # Strategy 4: Match by keywords (first 3-4 significant words)
        keywords = item.description.split()
        if not keywords:
            return None

        # Filter by most significant keywords (skip common words)
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
            # Multiple matches - prefer parts with this supplier
            supplier_parts = SupplierPart.objects.filter(
                part__in=query, supplier=supplier
            )
            if supplier_parts.exists():
                return supplier_parts.first().part
            return query.first()

        return None

    @admin.action(description='Create Purchase Order from invoice')
    def create_purchase_order(self, request, queryset):
        """Create Purchase Order with line items for all matched invoice items."""
        created_orders = 0

        for invoice in queryset:
            # Check if invoice has any matched items
            matched_items = invoice.items.filter(matched=True)
            if not matched_items.exists():
                self.message_user(
                    request,
                    f'Invoice {invoice.invoice_number} has no matched items. Match parts first.',
                    messages.ERROR,
                )
                continue

            # Check if PO with this reference already exists
            if PurchaseOrder.objects.filter(reference=invoice.invoice_number).exists():
                self.message_user(
                    request,
                    f'Purchase Order with reference {invoice.invoice_number} already exists.',
                    messages.WARNING,
                )
                continue

            try:
                # Create Purchase Order
                po = PurchaseOrder.objects.create(
                    reference=invoice.invoice_number,
                    supplier=invoice.supplier,
                    description=f'Auto-created from invoice {invoice.invoice_number}',
                    target_date=invoice.invoice_date,
                )

                lines_created = 0

                # Create line items for each matched invoice item
                for item in matched_items:
                    # Get SupplierPart for this part and supplier
                    supplier_part = SupplierPart.objects.filter(
                        part=item.part, supplier=invoice.supplier
                    ).first()

                    if not supplier_part:
                        # Create SupplierPart if it doesn't exist
                        supplier_part = SupplierPart.objects.create(
                            part=item.part,
                            supplier=invoice.supplier,
                            SKU=item.seller_sku or f'AUTO-{item.part.pk}',
                            description=item.description[:250],
                        )

                    # Create PurchaseOrderLineItem
                    PurchaseOrderLineItem.objects.create(
                        order=po,
                        part=supplier_part,
                        quantity=item.quantity,
                        purchase_price=Decimal(str(item.unit_price)),
                        reference=item.seller_sku or '',
                    )
                    lines_created += 1

                created_orders += 1

                # Log the creation
                InvoiceProcessingLog.objects.create(
                    invoice=invoice,
                    action='PO_CREATE',
                    message=f'Created Purchase Order {po.reference} with {lines_created} line items',
                )

                # Update invoice status
                invoice.status = 'COMPLETED'
                invoice.save()

            except Exception as e:
                InvoiceProcessingLog.objects.create(
                    invoice=invoice,
                    action='STOCK_ERROR',
                    message=f'Failed to create Purchase Order: {e!s}',
                )
                self.message_user(
                    request,
                    f'Failed to create PO for {invoice.invoice_number}: {e!s}',
                    messages.ERROR,
                )

        if created_orders > 0:
            self.message_user(
                request,
                f'Created {created_orders} Purchase Order(s). Go to Purchasing > Purchase Orders to review and receive items.',
                messages.SUCCESS,
            )

    @admin.action(description='Reset invoice (clear data and items)')
    def reset_invoice(self, request, queryset):
        """Reset invoice to pending state."""
        for invoice in queryset:
            invoice.items.all().delete()
            invoice.status = 'PENDING'
            invoice.invoice_data = {}
            invoice.processing_log = ''
            invoice.error_message = ''
            invoice.save()

        self.message_user(
            request, f'Reset {queryset.count()} invoices', messages.SUCCESS
        )


@admin.register(InvoiceItem)
class InvoiceItemAdmin(admin.ModelAdmin):
    """Admin interface for invoice items."""

    list_display = (
        'invoice_number',
        'part_display',
        'description',
        'quantity',
        'unit_price',
        'matched_badge',
    )
    list_filter = ('matched', 'invoice__supplier', 'invoice__created_at')
    search_fields = ('description', 'seller_sku', 'invoice__invoice_number')
    readonly_fields = (
        'invoice',
        'description',
        'seller_sku',
        'quantity',
        'unit_price',
        'total_price',
    )

    def invoice_number(self, obj):
        """Return invoice number."""
        return obj.invoice.invoice_number

    invoice_number.short_description = 'Invoice'

    def part_display(self, obj):
        """Return part display with styling."""
        if obj.part:
            return format_html(
                '<strong style="color: green;">{}</strong>', obj.part.name
            )
        return format_html('<span style="color: red;">No Part</span>')

    part_display.short_description = 'Part'

    def matched_badge(self, obj):
        """Return match status badge."""
        if obj.matched:
            return format_html('<span style="color: green;">✓ Matched</span>')
        return format_html('<span style="color: red;">✗ Unmatched</span>')

    matched_badge.short_description = 'Status'


@admin.register(InvoiceProcessingLog)
class InvoiceProcessingLogAdmin(admin.ModelAdmin):
    """Admin interface for processing logs."""

    list_display = ('invoice_number', 'action', 'created_at', 'message_preview')
    list_filter = ('action', 'invoice__supplier', 'created_at')
    search_fields = ('invoice__invoice_number', 'message')
    readonly_fields = ('invoice', 'action', 'message', 'created_at')

    def invoice_number(self, obj):
        """Return invoice number."""
        return obj.invoice.invoice_number

    invoice_number.short_description = 'Invoice'

    def message_preview(self, obj):
        """Return message preview."""
        return obj.message[:100] + '...' if len(obj.message) > 100 else obj.message

    message_preview.short_description = 'Message'

    def has_add_permission(self, request):
        """Disable add permission."""
        return False

    def has_delete_permission(self, request, obj=None):
        """Allow delete permission for cascade delete."""
        # Allow deletion (needed for cascade delete when deleting Invoice)
        return True
