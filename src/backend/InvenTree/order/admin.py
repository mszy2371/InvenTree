"""Admin functionality for the 'order' app."""

from django.contrib import admin, messages
from django.db import transaction

from order import models
from stock.models import StockItem, StockLocation


class GeneralExtraLineAdmin:
    """Admin class template for the 'ExtraLineItem' models."""

    list_display = ('order', 'quantity', 'reference')

    search_fields = ['order__reference', 'order__customer__name', 'reference']

    autocomplete_fields = ('order',)


class GeneralExtraLineMeta:
    """Metaclass template for the 'ExtraLineItem' models."""

    skip_unchanged = True
    report_skipped = False
    clean_model_instances = True


class PurchaseOrderLineItemInlineAdmin(admin.StackedInline):
    """Inline admin class for the PurchaseOrderLineItem model."""

    autocomplete_fields = ['part', 'destination', 'build_order']

    model = models.PurchaseOrderLineItem
    extra = 0


@admin.register(models.PurchaseOrder)
class PurchaseOrderAdmin(admin.ModelAdmin):
    """Admin class for the PurchaseOrder model."""

    exclude = ['reference_int']

    list_display = ('reference', 'supplier', 'status', 'description', 'creation_date')

    search_fields = ['reference', 'supplier__name', 'description']

    inlines = [PurchaseOrderLineItemInlineAdmin]

    autocomplete_fields = [
        'address',
        'contact',
        'created_by',
        'destination',
        'supplier',
        'project_code',
        'received_by',
        'responsible',
    ]

    actions = ['receive_all_items']

    @admin.action(description='Receive ALL items (auto-receive remaining quantities)')
    def receive_all_items(self, request, queryset):
        """Receive all remaining items for selected Purchase Orders."""
        total_received = 0
        total_items = 0
        errors = []

        for order in queryset:
            # Check if order is in PLACED status (status code 20)
            if order.status != 20:  # PurchaseOrderStatus.PLACED
                errors.append(
                    f"Order {order.reference}: cannot receive - status is not 'Placed'"
                )
                continue

            # Get default location (order destination or first available location)
            default_location = order.destination
            if not default_location:
                default_location = StockLocation.objects.filter(
                    structural=False
                ).first()

            if not default_location:
                errors.append(
                    f'Order {order.reference}: no destination location set and no locations available'
                )
                continue

            # Iterate through all line items
            for line_item in order.lines.all():
                remaining = line_item.quantity - line_item.received

                if remaining <= 0:
                    continue  # Already fully received

                if not line_item.part or not line_item.part.part:
                    errors.append(
                        f'Order {order.reference}: line item has no part assigned'
                    )
                    continue

                # Determine destination for this line item
                destination = line_item.destination or default_location

                try:
                    with transaction.atomic():
                        # Create stock item
                        StockItem.objects.create(
                            part=line_item.part.part,  # SupplierPart -> Part
                            supplier_part=line_item.part,
                            location=destination,
                            quantity=remaining,
                            purchase_order=order,
                            purchase_price=line_item.purchase_price,
                        )

                        # Update line item received count
                        line_item.received += remaining
                        line_item.save()

                        total_received += remaining
                        total_items += 1

                except Exception as e:
                    errors.append(
                        f'Order {order.reference}, line {line_item.pk}: {e!s}'
                    )

            # Check if order should be marked as complete
            order.refresh_from_db()
            all_received = all(
                line.received >= line.quantity for line in order.lines.all()
            )

            if all_received:
                # Mark order as complete (status 30)
                order.status = 30  # PurchaseOrderStatus.COMPLETE
                order.save()

        # Show results
        if total_items > 0:
            self.message_user(
                request,
                f'Successfully received {total_received} units across {total_items} line items.',
                messages.SUCCESS,
            )

        if errors:
            for error in errors[:5]:  # Show first 5 errors
                self.message_user(request, error, messages.WARNING)
            if len(errors) > 5:
                self.message_user(
                    request, f'... and {len(errors) - 5} more errors', messages.WARNING
                )


class SalesOrderLineItemInlineAdmin(admin.StackedInline):
    """Inline admin class for the SalesOrderLineItem model."""

    model = models.SalesOrderLineItem
    extra = 0

    autocomplete_fields = ['part']


@admin.register(models.SalesOrder)
class SalesOrderAdmin(admin.ModelAdmin):
    """Admin class for the SalesOrder model."""

    exclude = ['reference_int']

    list_display = ('reference', 'customer', 'status', 'description', 'creation_date')

    search_fields = ['reference', 'customer__name', 'description']

    inlines = [SalesOrderLineItemInlineAdmin]

    autocomplete_fields = [
        'address',
        'contact',
        'created_by',
        'customer',
        'project_code',
        'responsible',
        'shipped_by',
    ]


@admin.register(models.PurchaseOrderLineItem)
class PurchaseOrderLineItemAdmin(admin.ModelAdmin):
    """Admin class for the PurchaseOrderLine model."""

    list_display = ('order', 'part', 'quantity', 'reference')

    search_fields = ('reference',)

    autocomplete_fields = ('order', 'part', 'destination')


@admin.register(models.PurchaseOrderExtraLine)
class PurchaseOrderExtraLineAdmin(GeneralExtraLineAdmin, admin.ModelAdmin):
    """Admin class for the PurchaseOrderExtraLine model."""


@admin.register(models.SalesOrderLineItem)
class SalesOrderLineItemAdmin(admin.ModelAdmin):
    """Admin class for the SalesOrderLine model."""

    list_display = ('order', 'part', 'quantity', 'reference')

    search_fields = [
        'part__name',
        'order__reference',
        'order__customer__name',
        'reference',
    ]

    autocomplete_fields = ('order', 'part')


@admin.register(models.SalesOrderExtraLine)
class SalesOrderExtraLineAdmin(GeneralExtraLineAdmin, admin.ModelAdmin):
    """Admin class for the SalesOrderExtraLine model."""


@admin.register(models.SalesOrderShipment)
class SalesOrderShipmentAdmin(admin.ModelAdmin):
    """Admin class for the SalesOrderShipment model."""

    list_display = ['order', 'shipment_date', 'reference']

    search_fields = ['reference', 'order__reference', 'order__customer__name']

    autocomplete_fields = ('order', 'checked_by')


@admin.register(models.SalesOrderAllocation)
class SalesOrderAllocationAdmin(admin.ModelAdmin):
    """Admin class for the SalesOrderAllocation model."""

    list_display = ('line', 'item', 'quantity')

    autocomplete_fields = ('line', 'shipment', 'item')


@admin.register(models.ReturnOrder)
class ReturnOrderAdmin(admin.ModelAdmin):
    """Admin class for the ReturnOrder model."""

    exclude = ['reference_int']

    list_display = ['reference', 'customer', 'status']

    search_fields = ['reference', 'customer__name', 'description']

    autocomplete_fields = ['customer', 'project_code', 'contact', 'address']


@admin.register(models.ReturnOrderLineItem)
class ReturnOrderLineItemAdmin(admin.ModelAdmin):
    """Admin class for ReturnOrderLine model."""

    list_display = ['order', 'item', 'reference']

    autocomplete_fields = ['item', 'order']


@admin.register(models.ReturnOrderExtraLine)
class ReturnOrdeerExtraLineAdmin(GeneralExtraLineAdmin, admin.ModelAdmin):
    """Admin class for the ReturnOrderExtraLine model."""
