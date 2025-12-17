#!/usr/bin/env python
"""Quick import script.

Run from project root: python scripts/import_supplier_products.py.
"""

import os
import sys

import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'InvenTree.settings')
sys.path.insert(0, '/home/inventree/src/backend/InvenTree')
django.setup()

from importer.import_supplier_products import SupplierProductImporter

# Define your CSV files and supplier names
CSV_FILES_WITH_SUPPLIERS = [
    ('/home/inventree/dev/marcina/price-list-2025-11 cherry.csv', 'Cherry Cosmetics'),
    (
        '/home/inventree/dev/marcina/Connect Beauty Base Price List VR T3 (4).csv',
        'Connect Beauty',
    ),
    ('/home/inventree/dev/marcina/shure_cosmetics_pricelist.csv', 'Shure Cosmetics'),
]

if __name__ == '__main__':
    importer = SupplierProductImporter(verbose=True)

    try:
        summary = importer.import_all(CSV_FILES_WITH_SUPPLIERS)

        print('\n' + '=' * 60)
        print('✅ IMPORT SUCCESSFUL!')
        print('=' * 60)
        print(f'Parts created: {summary["created_parts"]}')
        print(f'SupplierParts created: {summary["created_supplier_parts"]}')
        print(f'Total unique products: {summary["total_unique_products"]}')

        if summary['errors']:
            print(f'\nWarnings/Errors ({len(summary["errors"])}):')
            for error in summary['errors']:
                print(f'  ⚠ {error}')

    except Exception as e:
        print(f'\n❌ IMPORT FAILED: {e!s}')
        import traceback

        traceback.print_exc()
        sys.exit(1)
