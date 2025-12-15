"""Import supplier products from multiple CSV files with deduplication by barcode.

Automatically creates Parts and links them as SupplierParts to multiple suppliers.
"""

import csv
import hashlib
import logging
from decimal import Decimal
from typing import Optional

from django.core.management.base import CommandError

from company.models import Company, SupplierPart
from part.models import Part, PartCategory

logger = logging.getLogger(__name__)


class SupplierProductImporter:
    """Importer for supplier products with deduplication by barcode."""

    def __init__(self, verbose=False):
        """Initialize importer."""
        self.verbose = verbose
        self.products_by_barcode: dict[str, dict] = {}  # barcode -> product data
        self.files_data = []  # List of (filename, supplier_name, data)
        self.created_parts = []
        self.created_supplier_parts = []
        self.errors = []
        self.log_messages = []  # Collect all log messages
        self.used_ipns = set()  # Track IPNs used in this session

    def log(self, msg):
        """Log message if verbose."""
        self.log_messages.append(msg)
        if self.verbose:
            logger.info(msg)
        print(msg)

    def get_log(self) -> str:
        """Get all collected log messages as a string."""
        return '\n'.join(self.log_messages)

    def load_csv_file(self, filepath: str, supplier_name: str) -> list[dict]:
        """Load CSV file and return list of product dictionaries.

        Automatically detects column positions and format (Very vs standard).
        """
        self.log(f'\n📂 Loading file: {filepath}')

        try:
            with open(filepath, encoding='utf-8') as f:
                # Read first few lines to detect format
                first_lines = [next(f) for _ in range(10)]

            # Check if it's Very format (has "PRICE LIST" header)
            is_very_format = any('PRICE LIST' in line for line in first_lines)

            # Check if it's WTS format (has ProdCode column)
            is_wts_format = any('ProdCode' in line for line in first_lines)

            if is_very_format:
                products = self._load_very_csv(filepath)
            elif is_wts_format:
                products = self._load_wts_csv(filepath)
            else:
                products = self._load_standard_csv(filepath)

            self.log(f'   ✓ Found {len(products)} products')
            self.files_data.append((filepath, supplier_name, products))
            return products

        except Exception as e:
            error_msg = f'Error loading {filepath}: {e!s}'
            self.errors.append(error_msg)
            self.log(f'   ✗ {error_msg}')
            return []

    def _load_standard_csv(self, filepath: str) -> list[dict]:
        """Load standard format CSV (Shure, Connect Beauty, Cherry).

        Normalizes various column names to standard format.
        """
        with open(filepath, encoding='utf-8-sig') as f:  # utf-8-sig handles BOM
            reader = csv.DictReader(f)
            products = []
            for row in reader:
                # Normalize column names to handle different formats
                normalized = {
                    'SKU': row.get('SKU', '') or row.get('Product Code', '') or '',
                    'Product Name': row.get('Product Name', '')
                    or row.get('Name', '')
                    or row.get('Title', '')
                    or '',
                    'Brand': row.get('Brand', '') or row.get('Manufacturer', '') or '',
                    'Barcode': row.get('Barcode', '') or row.get('EAN', '') or '',
                    'Category': row.get('Category', '') or '',
                    'Unit Price (GBP)': row.get('Unit Price (GBP)', '')
                    or row.get('Unit Price', '')
                    or row.get('Price', '')
                    or '',
                }
                # Only add if we have a valid product name and SKU (skip N/A)
                if (
                    normalized['Product Name']
                    and normalized['SKU']
                    and normalized['SKU'].upper() != 'N/A'
                ):
                    products.append(normalized)
            return products

    def _load_wts_csv(self, filepath: str) -> list[dict]:
        """Load WTS format CSV - normalize to standard columns."""
        with open(filepath, encoding='utf-8') as f:
            reader = csv.DictReader(f)
            products = []
            for row in reader:
                # Normalize WTS format to standard
                # WTS columns: ProdCode, Product Description, Price (in £ Price column)
                normalized = {
                    'SKU': row.get('ProdCode', ''),
                    'Product Name': row.get('Product Description', ''),
                    'Brand': '',
                    'Barcode': row.get('Barcode', '') or '',
                    'Category': '',
                    'Unit Price (GBP)': row.get('Each', '')
                    or row.get('Price', '')
                    or '',
                }
                if normalized['Product Name']:
                    products.append(normalized)
            return products

    def _load_very_csv(self, filepath: str) -> list[dict]:
        """Load Very format CSV - skip header lines, normalize column names."""
        with open(filepath, encoding='utf-8') as f:
            lines = f.readlines()

        # Find the header line (contains "Title,-,Barcode")
        header_idx = None
        for i, line in enumerate(lines):
            if 'Title' in line and 'Barcode' in line:
                header_idx = i
                break

        if header_idx is None:
            return []

        # Read from header line onwards
        csv_content = ''.join(lines[header_idx:])
        reader = csv.DictReader(csv_content.splitlines())

        products = []
        for row in reader:
            # Normalize to standard format
            normalized = {
                'SKU': row.get('Title', '')[:100],  # Use Title as SKU (first 100 chars)
                'Product Name': row.get('Title', ''),
                'Brand': 'Very Cosmetics',
                'Barcode': row.get('Barcode', '') or '',
                'Category': '',
                'Unit Price (GBP)': row.get('Price', '')
                or row.get('Compare at price', '')
                or '',
            }
            if normalized['Product Name']:
                products.append(normalized)

        return products

    def extract_price(self, price_str: str) -> Optional[Decimal]:
        """Extract price from string (handle £, €, etc)."""
        if not price_str:
            return None

        # Remove currency symbols and extra text
        price_str = price_str.replace('£', '').replace('€', '').split('each')[0].strip()

        try:
            return Decimal(price_str)
        except:
            return None

    def generate_ipn(self, sku: str, name: str) -> str:
        """Generate IPN based on SKU hash.

        Format: IPN-XXXXXXXX where XXXXXXXX is MD5 hash of SKU+name.
        """
        hash_input = f'{sku}-{name}'.encode()
        hash_hex = hashlib.md5(hash_input).hexdigest()[:8].upper()
        return f'IPN-{hash_hex}'

    def deduplicate_products(self) -> dict[str, dict]:
        """Process products by SKU (not barcode).

        Each SKU is unique, so we keep all products.
        Returns dict: sku -> product data.
        """
        self.log('\n🔄 Processing products by SKU...')

        products_by_sku = {}

        for _filename, supplier_name, products in self.files_data:
            for product in products:
                # Extract SKU - this is unique per supplier
                sku = product.get('SKU', '').strip()

                if not sku:
                    self.log(
                        f'   ⚠ No SKU for: {product.get("Product Name", product.get("Title", "Unknown"))}'
                    )
                    continue

                if sku not in products_by_sku:
                    # First occurrence - create product record
                    category_name = product.get('Category') or ''
                    category_obj = (
                        self.get_or_create_category(category_name)
                        if category_name
                        else None
                    )

                    products_by_sku[sku] = {
                        'sku': sku,
                        'name': product.get('Product Name')
                        or product.get('Title')
                        or 'Unknown',
                        'brand': product.get('Brand')
                        or product.get('Manufacturer')
                        or '',
                        'category': category_name,
                        'category_obj': category_obj,
                        'barcode': product.get('Barcode') or product.get('EAN') or '',
                        'suppliers': {},  # supplier_name -> {sku, price, ...}
                    }

                # Add this supplier's info
                products_by_sku[sku]['suppliers'][supplier_name] = {
                    'sku': sku,
                    'price': self.extract_price(
                        product.get('Unit Price (GBP)')
                        or product.get('Unit Price')
                        or product.get('Price', '')
                    ),
                    'barcode': product.get('Barcode') or product.get('EAN') or '',
                    'currency': 'GBP',
                }

        self.log(f'   ✓ Total products by SKU: {len(products_by_sku)}')
        return products_by_sku

    def get_or_create_supplier(self, supplier_name: str) -> Company:
        """Get or create a supplier company."""
        company, created = Company.objects.get_or_create(
            name=supplier_name,
            defaults={'description': f'Supplier: {supplier_name}', 'is_supplier': True},
        )
        if created:
            self.log(f'   ✓ Created supplier: {supplier_name}')
        return company

    def get_or_create_category(self, category_name: str) -> Optional[PartCategory]:
        """Get or create a part category."""
        if not category_name:
            return None

        category, _created = PartCategory.objects.get_or_create(
            name=category_name, defaults={'description': f'Category: {category_name}'}
        )
        return category

    def create_part(self, product_data: dict) -> Optional[Part]:
        """Create a Part from product data with custom IPN based on SKU.

        If Part with same IPN already exists, returns existing Part.
        """
        try:
            # Truncate name to max 100 chars (model limit)
            name = product_data['name'][:100]

            # Generate IPN from SKU+name
            ipn = self.generate_ipn(product_data['sku'], product_data['name'])

            # Check if Part with this IPN already exists in DB
            existing_part = Part.objects.filter(IPN=ipn).first()
            if existing_part:
                self.log(f'   ⏭ Part already exists: {existing_part.name} (IPN: {ipn})')
                return existing_part

            # Check if we already created this in current session
            if ipn in self.used_ipns:
                self.log(f'   ⏭ Part with IPN {ipn} already created in this session')
                return None

            # Track this IPN as used
            self.used_ipns.add(ipn)

            # Create part with custom IPN
            part = Part.objects.create(
                name=name,
                description=product_data.get('brand', ''),
                category=product_data.get('category_obj'),
                component=True,
                IPN=ipn,
            )

            self.log(f'   ✓ Created Part: {part.name} (IPN: {part.IPN})')
            self.created_parts.append(part)
            return part

        except Exception as e:
            error_msg = f'Error creating part for {product_data["name"]}: {e!s}'
            self.errors.append(error_msg)
            self.log(f'   ✗ {error_msg}')
            return None

    def create_supplier_part(
        self, part: Part, supplier: Company, sku: str, price: Optional[Decimal]
    ) -> bool:
        """Create a SupplierPart linking Part to Supplier."""
        try:
            supplier_part, created = SupplierPart.objects.get_or_create(
                part=part,
                supplier=supplier,
                SKU=sku,
                defaults={'note': f'Imported from {supplier.name}'},
            )

            if price and created:
                # Try to set price if model allows it
                try:
                    supplier_part.base_cost = price
                    supplier_part.save()
                except:
                    pass

            if created:
                self.log(f'      • Created SupplierPart: {sku} @ {supplier.name}')
                self.created_supplier_parts.append(supplier_part)
                return True
            else:
                self.log(
                    f'      • SupplierPart already exists: {sku} @ {supplier.name}'
                )
                return False

        except Exception as e:
            error_msg = f'Error creating SupplierPart {sku}: {e!s}'
            self.errors.append(error_msg)
            self.log(f'      ✗ {error_msg}')
            return False

    def import_all(self, csv_files_with_suppliers: list[tuple[str, str]]) -> dict:
        """Import all CSV files with their supplier names.

        Args:
            csv_files_with_suppliers: List of tuples (filepath, supplier_name)

        Returns:
            Dict with import statistics
        """
        self.log('=' * 60)
        self.log('🚀 Starting Supplier Product Import')
        self.log('=' * 60)

        # Step 1: Load all files
        for filepath, supplier_name in csv_files_with_suppliers:
            self.load_csv_file(filepath, supplier_name)

        if not self.files_data:
            raise CommandError('No CSV files loaded successfully')

        # Step 2: Get products by SKU (not barcode)
        products_by_sku = self.deduplicate_products()

        # Step 3: Create parts and supplier parts
        total_products = len(products_by_sku)
        self.log(
            f'\n📦 Creating Parts and SupplierParts ({total_products} products)...\n'
        )

        for idx, (_sku, product_data) in enumerate(products_by_sku.items(), 1):
            # Progress log every 50 products or at start/end
            if idx == 1 or idx % 50 == 0 or idx == total_products:
                progress = (idx / total_products) * 100
                self.log(f'⏳ Progress: {idx}/{total_products} ({progress:.1f}%)')

            # Create Part
            part = self.create_part(product_data)
            if not part:
                continue

            # Create SupplierPart for each supplier
            for supplier_name, supplier_info in product_data['suppliers'].items():
                supplier = self.get_or_create_supplier(supplier_name)
                self.create_supplier_part(
                    part=part,
                    supplier=supplier,
                    sku=supplier_info['sku'],
                    price=supplier_info['price'],
                )

        # Summary
        self.log('\n' + '=' * 60)
        self.log('✅ Import Complete')
        self.log('=' * 60)

        summary = {
            'created_parts': len(self.created_parts),
            'created_supplier_parts': len(self.created_supplier_parts),
            'total_products': len(products_by_sku),
            'errors': self.errors,
        }

        self.log('\n📊 Summary:')
        self.log(f'   • Parts created: {summary["created_parts"]}')
        self.log(f'   • SupplierParts created: {summary["created_supplier_parts"]}')
        self.log(f'   • Total products: {summary["total_products"]}')
        if self.errors:
            self.log(f'   • Errors: {len(self.errors)}')
            for error in self.errors:
                self.log(f'     - {error}')

        return summary

        return summary
