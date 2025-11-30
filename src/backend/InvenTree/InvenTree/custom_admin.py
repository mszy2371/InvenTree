"""Custom Django Admin Site with Import Supplier Products button."""

import os

from django.contrib import admin, messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import path

from importer.admin_actions import ImportSupplierProductsForm

from importer.import_supplier_products import SupplierProductImporter


class CustomAdminSite(admin.AdminSite):
    """Custom admin site with additional features."""

    site_header = 'InvenTree Admin'
    site_title = 'InvenTree'
    index_title = 'Witaj w panelu administracyjnym'

    def get_urls(self):
        """Add custom admin URLs."""
        urls = super().get_urls()
        custom_urls = [
            path(
                'import-supplier-products/',
                self.admin_site_wrapper(self.import_supplier_products_view),
                name='import_supplier_products',
            )
        ]
        return custom_urls + urls

    def admin_site_wrapper(self, view):
        """Wrap view with admin site context."""

        @login_required(login_url='/admin/login/')
        def wrapper(request):
            return view(request)

        return wrapper

    def index(self, request, extra_context=None):
        """Override admin index to add import button."""
        extra_context = extra_context or {}

        # Dodaj link do importu
        extra_context['import_products_url'] = '/admin/import-supplier-products/'

        return super().index(request, extra_context)

    def import_supplier_products_view(self, request):
        """View for importing supplier products."""
        if not request.user.is_staff:
            return HttpResponseRedirect('/admin/login/')

        if request.method == 'POST':
            form = ImportSupplierProductsForm(request.POST)
            if form.is_valid():
                folder = form.cleaned_data['folder']
                supplier_names_str = form.cleaned_data['supplier_names']
                verbose = form.cleaned_data.get('verbose', False)

                # Parse supplier names
                supplier_names = [s.strip() for s in supplier_names_str.split(',')]

                # Get CSV files from folder
                if not os.path.isdir(folder):
                    form.add_error('folder', f'Folder {folder} nie istnieje')
                    context = {
                        'form': form,
                        'site_header': self.site_header,
                        'title': 'Import produktów dostawców',
                    }
                    return render(
                        request, 'admin/import_supplier_products.html', context
                    )

                csv_files = sorted([
                    f for f in os.listdir(folder) if f.endswith('.csv')
                ])

                if len(csv_files) != len(supplier_names):
                    form.add_error(
                        'supplier_names',
                        f'Liczba hurtowni ({len(supplier_names)}) nie zgadza się z liczbą plików CSV ({len(csv_files)}). '
                        f'Znaleźliśmy: {", ".join(csv_files)}',
                    )
                    context = {
                        'form': form,
                        'site_header': self.site_header,
                        'title': 'Import produktów dostawców',
                    }
                    return render(
                        request, 'admin/import_supplier_products.html', context
                    )

                # Prepare CSV files with suppliers
                csv_files_with_suppliers = [
                    (os.path.join(folder, csv_file), supplier)
                    for csv_file, supplier in zip(
                        csv_files, supplier_names, strict=True
                    )
                ]

                # Run import
                try:
                    importer = SupplierProductImporter(verbose=verbose)
                    summary = importer.import_all(csv_files_with_suppliers)

                    # Store results in session
                    request.session['import_summary'] = {
                        'created_parts': summary['created_parts'],
                        'created_supplier_parts': summary['created_supplier_parts'],
                        'total_unique_products': summary['total_unique_products'],
                        'errors': summary.get('errors', []),
                    }

                    messages.success(
                        request,
                        f'✅ Import zakończony! '
                        f'{summary["created_parts"]} Parts, '
                        f'{summary["created_supplier_parts"]} SupplierParts',
                    )

                    return HttpResponseRedirect(request.path)

                except Exception as e:
                    messages.error(request, f'❌ Błąd importu: {e!s}')
                    form.add_error(None, str(e))
                    context = {
                        'form': form,
                        'site_header': self.site_header,
                        'title': 'Import produktów dostawców',
                    }
                    return render(
                        request, 'admin/import_supplier_products.html', context
                    )
        else:
            form = ImportSupplierProductsForm()

        # Get last import summary from session
        summary = request.session.pop('import_summary', None)

        context = {
            'form': form,
            'summary': summary,
            'site_header': self.site_header,
            'title': 'Import produktów dostawców',
        }
        return render(request, 'admin/import_supplier_products.html', context)
