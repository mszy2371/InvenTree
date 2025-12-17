"""Django management command to import supplier products from CSV files.

Usage:
    python manage.py import_supplier_products --files /path/to/file1.csv "Supplier 1" /path/to/file2.csv "Supplier 2"
    python manage.py import_supplier_products --folder /path/to/folder --suppliers "Supplier1" "Supplier2" "Supplier3"
"""

from django.core.management.base import BaseCommand, CommandError

from importer.import_supplier_products import SupplierProductImporter


class Command(BaseCommand):
    """Django management command to import supplier products."""

    help = 'Import supplier products from CSV files with deduplication by barcode'

    def add_arguments(self, parser):
        """Add command arguments."""
        parser.add_argument(
            '--files',
            nargs='+',
            help='CSV files and their supplier names: file1.csv "Supplier1" file2.csv "Supplier2"',
        )
        parser.add_argument('--folder', type=str, help='Folder containing CSV files')
        parser.add_argument(
            '--suppliers',
            nargs='+',
            help='Supplier names (in order of CSV files in folder)',
        )
        parser.add_argument('--verbose', action='store_true', help='Verbose output')

    def handle(self, *args, **options):
        """Execute the command."""
        importer = SupplierProductImporter(verbose=options['verbose'])

        csv_files_with_suppliers = []

        if options['files']:
            # Parse --files format: file1 supplier1 file2 supplier2 ...
            files = options['files']
            if len(files) % 2 != 0:
                raise CommandError(
                    'Invalid format for --files. Use: file1.csv "Supplier1" file2.csv "Supplier2"'
                )
            for i in range(0, len(files), 2):
                csv_files_with_suppliers.append((files[i], files[i + 1]))

        elif options['folder'] and options['suppliers']:
            # Parse folder with supplier names
            import os

            folder = options['folder']
            suppliers = options['suppliers']

            csv_files = sorted([f for f in os.listdir(folder) if f.endswith('.csv')])

            if len(csv_files) != len(suppliers):
                raise CommandError(
                    f'Number of CSV files ({len(csv_files)}) does not match '
                    f'number of suppliers ({len(suppliers)})'
                )

            for csv_file, supplier in zip(csv_files, suppliers, strict=True):
                filepath = os.path.join(folder, csv_file)
                csv_files_with_suppliers.append((filepath, supplier))

        else:
            raise CommandError(
                'Provide either --files or both --folder and --suppliers'
            )

        # Run import
        try:
            summary = importer.import_all(csv_files_with_suppliers)
            self.stdout.write(
                self.style.SUCCESS(
                    f'\n✅ Successfully imported {summary["created_parts"]} parts '
                    f'and {summary["created_supplier_parts"]} supplier parts'
                )
            )
        except Exception as e:
            raise CommandError(f'Import failed: {e!s}')
