"""Admin site specification for the 'importer' app."""

import logging

from django.contrib import admin, messages

import importer.models
import importer.registry
from importer.import_supplier_products import SupplierProductImporter

logger = logging.getLogger(__name__)


class DataImportColumnMapAdmin(admin.TabularInline):
    """Inline admin for DataImportColumnMap model."""

    model = importer.models.DataImportColumnMap
    can_delete = False
    max_num = 0

    def get_readonly_fields(self, request, obj=None):
        """Return the readonly fields for the admin interface."""
        return ['field']

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        """Override the choices for the column field."""
        if db_field.name == 'column':
            # TODO: Implement this!
            queryset = self.get_queryset(request)

            if queryset.count() > 0:
                session = queryset.first().session
                db_field.choices = [(col, col) for col in session.columns]

        return super().formfield_for_choice_field(db_field, request, **kwargs)


@admin.register(importer.models.DataImportSession)
class DataImportSessionAdmin(admin.ModelAdmin):
    """Admin interface for the DataImportSession model."""

    list_display = ['id', 'data_file', 'status', 'user']

    list_filter = ['status']

    inlines = [DataImportColumnMapAdmin]

    def get_readonly_fields(self, request, obj=None):
        """Update the readonly fields for the admin interface."""
        fields = ['columns', 'status', 'timestamp']

        # Prevent data file from being edited after upload!
        if obj:
            fields += ['data_file']
        else:
            fields += ['field_mapping']

        return fields

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        """Override the choices for the model_type field."""
        if db_field.name == 'model_type':
            db_field.choices = importer.registry.supported_model_options()

        return super().formfield_for_dbfield(db_field, request, **kwargs)


@admin.register(importer.models.DataImportRow)
class DataImportRowAdmin(admin.ModelAdmin):
    """Admin interface for the DataImportRow model."""

    list_display = ['id', 'session', 'row_index']

    def get_readonly_fields(self, request, obj=None):
        """Return the readonly fields for the admin interface."""
        return ['session', 'row_index', 'row_data', 'errors', 'valid']


def import_supplier_products_action(modeladmin, request, queryset):
    """Admin action to import supplier products from CSV."""
    if queryset.count() > 1:
        modeladmin.message_user(
            request, 'Please select only one import session.', messages.ERROR
        )
        return

    session = queryset.first()

    if (
        session.status
        != importer.models.SupplierProductImportSession.StatusChoices.PENDING
    ):
        modeladmin.message_user(
            request, 'Import session must be in PENDING status.', messages.ERROR
        )
        return

    try:
        # Update status
        session.status = (
            importer.models.SupplierProductImportSession.StatusChoices.IN_PROGRESS
        )
        session.save()

        # Read CSV file
        session.data_file.open('rb')
        csv_content = session.data_file.read().decode('utf-8')
        session.data_file.close()

        # Save to temp file for importer
        import tempfile

        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.csv', delete=False, encoding='utf-8'
        ) as tmp:
            tmp.write(csv_content)
            tmp_path = tmp.name

        # Run import
        product_importer = SupplierProductImporter(verbose=True)
        result = product_importer.import_all([(tmp_path, session.supplier.name)])

        # Update session with results
        session.parts_created = result['created_parts']
        session.supplier_parts_created = result['created_supplier_parts']
        session.errors = result.get('errors', [])
        session.status = (
            importer.models.SupplierProductImportSession.StatusChoices.COMPLETED
        )

        # Save full import log
        session.import_log = product_importer.get_log()
        session.save()

        # Clean up temp file
        import os

        try:
            os.unlink(tmp_path)
        except:
            pass

        modeladmin.message_user(
            request,
            f'✅ Import completed!\n'
            f'• Parts created: {result["created_parts"]}\n'
            f'• Supplier parts created: {result["created_supplier_parts"]}',
            messages.SUCCESS,
        )

    except Exception as e:
        session.status = (
            importer.models.SupplierProductImportSession.StatusChoices.FAILED
        )
        session.errors = [str(e)]
        session.import_log = f'❌ Import failed: {e!s}'
        session.save()

        logger.error(f'Supplier product import failed: {e!s}', exc_info=True)
        modeladmin.message_user(request, f'❌ Import failed: {e!s}', messages.ERROR)


import_supplier_products_action.short_description = 'Import supplier products from CSV'


@admin.register(importer.models.SupplierProductImportSession)
class SupplierProductImportSessionAdmin(admin.ModelAdmin):
    """Admin interface for SupplierProductImportSession."""

    list_display = [
        'supplier',
        'status',
        'parts_created',
        'supplier_parts_created',
        'timestamp',
    ]
    list_filter = ['status', 'timestamp', 'supplier']
    search_fields = ['supplier__name']
    actions = [import_supplier_products_action]

    readonly_fields = [
        'parts_created',
        'supplier_parts_created',
        'errors',
        'import_log',
        'timestamp',
        'updated',
    ]

    fieldsets = (
        (
            'Basic Information',
            {'fields': ('supplier', 'status', 'timestamp', 'updated', 'user')},
        ),
        ('CSV File', {'fields': ('data_file',)}),
        (
            'Results',
            {
                'fields': (
                    'parts_created',
                    'supplier_parts_created',
                    'import_log',
                    'errors',
                ),
                'classes': ('collapse',),
            },
        ),
    )

    def get_readonly_fields(self, request, obj=None):
        """Make data_file readonly after upload."""
        fields = list(self.readonly_fields)
        if obj:  # If editing existing object
            fields.append('data_file')
        return fields
