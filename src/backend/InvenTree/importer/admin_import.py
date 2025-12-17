"""Django Admin action for importing supplier products.

Can be added to any admin to trigger the import.
"""

from django import forms
from django.contrib import admin, messages
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import path, reverse

from importer.import_supplier_products import SupplierProductImporter


class SupplierProductImportForm(forms.Form):
    """Form for uploading CSV files for import."""

    cherry_csv = forms.FileField(label='Cherry CSV file', required=True)
    cherry_supplier = forms.CharField(
        label='Cherry Supplier Name', initial='Cherry Cosmetics'
    )

    connect_beauty_csv = forms.FileField(label='Connect Beauty CSV file', required=True)
    connect_beauty_supplier = forms.CharField(
        label='Connect Beauty Supplier Name', initial='Connect Beauty'
    )

    shure_csv = forms.FileField(label='Shure CSV file', required=True)
    shure_supplier = forms.CharField(
        label='Shure Supplier Name', initial='Shure Cosmetics'
    )


class SupplierProductImporterAdmin(admin.ModelAdmin):
    """Admin interface for supplier product import."""

    def get_urls(self):
        """Add custom URLs for import."""
        urls = super().get_urls()
        custom_urls = [
            path(
                'import-supplier-products/',
                self.admin_site.admin_view(self.import_supplier_products_view),
                name='import-supplier-products',
            )
        ]
        return custom_urls + urls

    def import_supplier_products_view(self, request):
        """View for importing supplier products."""
        if request.method == 'POST':
            form = SupplierProductImportForm(request.POST, request.FILES)
            if form.is_valid():
                try:
                    # Save uploaded files temporarily
                    import os
                    import tempfile

                    with tempfile.TemporaryDirectory() as tmpdir:
                        csv_files = []

                        # Save each file
                        for field, supplier_field in [
                            ('cherry_csv', 'cherry_supplier'),
                            ('connect_beauty_csv', 'connect_beauty_supplier'),
                            ('shure_csv', 'shure_supplier'),
                        ]:
                            if field in request.FILES:
                                file_content = request.FILES[field].read()
                                filepath = os.path.join(
                                    tmpdir, request.FILES[field].name
                                )
                                with open(filepath, 'wb') as f:
                                    f.write(file_content)
                                supplier_name = form.cleaned_data[supplier_field]
                                csv_files.append((filepath, supplier_name))

                        # Run import
                        importer = SupplierProductImporter(verbose=True)
                        summary = importer.import_all(csv_files)

                        # Show success message
                        self.message_user(
                            request,
                            f'✅ Import successful!\n'
                            f'Parts created: {summary["created_parts"]}\n'
                            f'SupplierParts created: {summary["created_supplier_parts"]}\n'
                            f'Total unique products: {summary["total_unique_products"]}',
                            messages.SUCCESS,
                        )

                        return HttpResponseRedirect(reverse('admin:index'))

                except Exception as e:
                    self.message_user(
                        request, f'❌ Import failed: {e!s}', messages.ERROR
                    )
        else:
            form = SupplierProductImportForm()

        context = {
            'form': form,
            'title': 'Import Supplier Products',
            'opts': self.model._meta,
            'has_view_permission': self.has_view_permission(request),
        }

        return render(request, 'admin/import_supplier_products.html', context)


def import_supplier_products_admin_action(modeladmin, request, queryset):
    """Admin action to import supplier products."""
    return HttpResponseRedirect(reverse('admin:import-supplier-products'))


import_supplier_products_admin_action.short_description = (
    'Import Supplier Products from CSV'
)
