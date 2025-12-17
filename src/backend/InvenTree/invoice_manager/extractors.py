"""Invoice extractors for different suppliers."""

import re
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import pymupdf


class BaseInvoiceExtractor:
    """Base class for invoice extractors."""

    def convert_pdf_with_fitz(self, invoice_path: str) -> tuple[str, dict]:
        """Convert PDF to text and extract data."""
        raise NotImplementedError

    def _safe_int_convert(self, value: str, default: int = 0) -> int:
        try:
            return int(float(value))
        except (ValueError, TypeError):
            return default

    def _safe_float_convert(self, value: str, default: float = 0.0) -> float:
        try:
            # Remove common currency symbols and whitespace
            cleaned = re.sub(r'[£€$\s]', '', str(value))
            return float(cleaned)
        except (ValueError, TypeError):
            return default


class ShureExtractor(BaseInvoiceExtractor):
    """Extractor for Shure Cosmetics invoices."""

    def convert_pdf_with_fitz(self, invoice_path: str) -> tuple[str, dict]:
        """Convert PDF and extract invoice data."""
        invoice_list: list[Any] = []
        all_text: str = ''
        with pymupdf.open(invoice_path) as doc:
            for page in doc:
                text = page.get_text()
                all_text += text
                page_tables = page.find_tables()
                for table in page_tables:
                    item = table.extract()
                    for row in item:
                        row = [
                            cell.replace('\n', ' ') if isinstance(cell, str) else cell
                            for cell in row
                        ]
                        if len(row) == 2 and row[0] != 'Total:':
                            if invoice_list:
                                invoice_list[-1].append(row)
                        else:
                            invoice_list.append(row)

        converted_dict = self._process_invoice_data(invoice_list, all_text)
        return all_text, converted_dict

    def _process_invoice_data(
        self, invoice_list: list, all_text: str
    ) -> dict[str, Any]:
        result: dict[str, Any] = {'invoice_metadata': {}, 'products': []}

        self._extract_metadata(all_text, result)
        self._extract_products(invoice_list, result)
        self._extract_total(invoice_list, result)

        return result

    def _extract_metadata(self, all_text: str, result: dict) -> None:
        date_match = re.search(r'Date:\s*(\d{1,2}/\d{1,2}/\d{4})', all_text)
        if date_match:
            result['invoice_metadata']['invoice_date'] = date_match.group(1)

        order_id_match = re.search(r'Order id:\s*#(\w+)', all_text)
        if order_id_match:
            result['invoice_metadata']['invoice_no'] = order_id_match.group(1)

    def _extract_products(self, invoice_list: list, result: dict) -> None:
        for row in invoice_list:
            if self._is_header_row(row) or self._is_total_row(row):
                continue

            # Skip option rows (rows that have None in first column)
            if not row or not row[0]:
                continue

            if len(row) >= 5:
                product_data = self._parse_product_row(row)
                if product_data:
                    result['products'].append(product_data)

    def _is_header_row(self, row: list) -> bool:
        return len(row) >= 5 and row[0] == 'SKU' and row[1] == 'Product'

    def _is_total_row(self, row: list) -> bool:
        return len(row) >= 2 and (
            row[0] == 'Total:'
            or (isinstance(row[0], str) and row[0].startswith('Total'))
        )

    def _parse_product_row(self, row: list) -> dict | None:
        try:
            sku = row[0] if row[0] else ''
            # Get full description (may contain multiple lines)
            description = row[1] if row[1] else ''
            if '\n' in description:
                description = description.split('\n')[0]

            # Find columns with price/qty data
            # Skip None and empty cells to find the actual data
            real_cols = [col for col in row[2:] if col]  # Skip SKU and Product

            if len(real_cols) < 3:
                return None

            # real_cols should be: [Price, Qty, Total]
            unit_price_str = str(real_cols[0]) if real_cols[0] else '0'
            quantity_str = str(real_cols[1]) if real_cols[1] else '0'
            total_price_str = str(real_cols[2]) if real_cols[2] else '0'

            unit_price = self._safe_float_convert(
                unit_price_str.replace('£', '').replace('€', '')
            )
            quantity = self._safe_int_convert(quantity_str)
            total_price = self._safe_float_convert(
                total_price_str.replace('£', '').replace('€', '')
            )

            return {
                'description': description,
                'seller_sku': sku,
                'quantity': quantity,
                'tax': 20.0,
                'unit_price': unit_price,
                'total_price': total_price,
            }
        except (IndexError, ValueError, AttributeError):
            return None

    def _extract_total(self, invoice_list: list, result: dict) -> None:
        for row in invoice_list:
            if self._is_total_row(row):
                total_amount = self._safe_float_convert(row[1].replace('£', ''))
                result['invoice_metadata']['invoice_total'] = total_amount
                break


class WholesaleTradingExtractor(BaseInvoiceExtractor):
    """Extractor for Wholesale Trading Supplies invoices."""

    def convert_pdf_with_fitz(self, invoice_path: str) -> tuple[str, dict]:
        """Convert PDF and extract invoice data."""
        all_text, all_blocks = self._extract_pdf_content(invoice_path)
        result = self._process_invoice_data(all_blocks, all_text)
        return all_text, result

    def _extract_pdf_content(self, invoice_path: str) -> tuple[str, list]:
        all_blocks = []
        with pymupdf.open(invoice_path) as doc:
            for page_num in range(len(doc)):
                text_blocks = doc[page_num].get_text('blocks')
                all_blocks.extend(text_blocks)

        all_text = ' '.join(block[4] for block in all_blocks)
        return all_text, all_blocks

    def _process_invoice_data(self, all_blocks: list, all_text: str) -> dict[str, Any]:
        result: dict[str, Any] = {'invoice_metadata': {}, 'products': []}

        self._extract_products(all_blocks, result)
        self._extract_metadata(all_text, result)
        self._calculate_totals(result)

        return result

    def _extract_products(self, all_blocks: list, result: dict) -> None:
        product_pattern = re.compile(
            r'(\d+)\s+(.+?)\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)'
        )

        for block in all_blocks:
            block_text = block[4].replace('\n', ' ')
            match = product_pattern.search(block_text)
            if match:
                product_data = self._parse_product_match(match)
                if product_data:
                    result['products'].append(product_data)

    def _parse_product_match(self, match) -> dict | None:
        try:
            _case, desc, unit_price, net_price, vat, qty = match.groups()
            return {
                'description': desc.strip(),
                'seller_sku': None,
                'quantity': int(float(qty.strip())),
                'tax': float(vat.replace(',', '.')),
                'unit_price': float(unit_price.replace(',', '.')),
                'total_price': float(net_price.replace(',', '.')),
            }
        except (ValueError, AttributeError):
            return None

    def _extract_metadata(self, all_text: str, result: dict) -> None:
        patterns = {
            'invoice_no': r'Invoice\s*No\.?:?\s*(\d+)',
            'invoice_date': r'Invoice\s*Date\.?:?\s*([\d./-]+)',
            'order_no': r'Order\s*No\.?:?\s*(\d+)',
        }

        for key, pattern in patterns.items():
            match = re.search(pattern, all_text, re.IGNORECASE)
            if match:
                result['invoice_metadata'][key] = match.group(1)

    def _calculate_totals(self, result: dict) -> None:
        products = result['products']

        total_net_amount = sum(float(product['total_price']) for product in products)
        total_vat_amount = sum(
            float(product['total_price']) * float(product['tax']) / 100
            for product in products
        )
        invoice_total = total_net_amount + total_vat_amount

        result['invoice_metadata'].update({
            'total_net_amount': total_net_amount,
            'total_vat_amount': total_vat_amount,
            'invoice_total': float(
                Decimal(invoice_total).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            ),
        })


class VeryExtractor(BaseInvoiceExtractor):
    """Extractor for Very Cosmetics invoices."""

    def convert_pdf_with_fitz(self, invoice_path: str) -> tuple[str, dict]:
        """Convert PDF and extract invoice data."""
        all_text = ''

        with pymupdf.open(invoice_path) as doc:
            for page in doc:
                text = page.get_text()
                all_text += text

        result = self._process_invoice_data(all_text)
        return all_text, result

    def _process_invoice_data(self, all_text: str) -> dict[str, Any]:
        result: dict[str, Any] = {'invoice_metadata': {}, 'products': []}

        self._extract_metadata(all_text, result)
        self._extract_products(all_text, result)

        return result

    def _extract_metadata(self, all_text: str, result: dict) -> None:
        # Order number: #VC8795
        order_match = re.search(r'ORDER NO\s*#?(VC\d+)', all_text)
        if order_match:
            result['invoice_metadata']['invoice_no'] = order_match.group(1)

        # Order date: 18-08-2025
        date_match = re.search(r'ORDER DATE\s*([\d-]+)', all_text)
        if date_match:
            result['invoice_metadata']['invoice_date'] = date_match.group(1)

        # Total: £ 301.97
        total_match = re.search(r'TOTAL\s*:\s*£\s*([\d.,]+)', all_text)
        if total_match:
            result['invoice_metadata']['invoice_total'] = self._safe_float_convert(
                total_match.group(1)
            )

        # VAT
        vat_match = re.search(r'VAT\s*\([\d%]+\)\s*:\s*£\s*([\d.,]+)', all_text)
        if vat_match:
            result['invoice_metadata']['total_vat_amount'] = self._safe_float_convert(
                vat_match.group(1)
            )

        # Sub total
        subtotal_match = re.search(r'SUB TOTAL\s*:\s*£\s*([\d.,]+)', all_text)
        if subtotal_match:
            result['invoice_metadata']['total_net_amount'] = self._safe_float_convert(
                subtotal_match.group(1)
            )

    def _extract_products(self, all_text: str, result: dict) -> None:
        # Very format: Each product spans 5 lines:
        # Line 1: Product name
        # Line 2: Barcode (13 digits)
        # Line 3: Quantity
        # Line 4: Unit price (£ X.XX)
        # Line 5: Total price (£ X.XX)
        lines = all_text.split('\n')

        i = 0
        while i < len(lines) - 4:
            line = lines[i].strip()

            # Check if next line is a barcode (mostly digits, 8-14 chars)
            next_line = lines[i + 1].strip() if i + 1 < len(lines) else ''
            if (
                line
                and not line.startswith('£')
                and not line.startswith('ITEM')
                and re.match(r'^\d{8,14}$', next_line)
            ):
                # This looks like a product name followed by barcode
                description = line
                barcode = next_line
                qty_line = lines[i + 2].strip() if i + 2 < len(lines) else ''
                price_line = lines[i + 3].strip() if i + 3 < len(lines) else ''
                total_line = lines[i + 4].strip() if i + 4 < len(lines) else ''

                # Parse quantity (should be just a number)
                if qty_line.isdigit():
                    quantity = int(qty_line)
                    unit_price = self._safe_float_convert(
                        price_line.replace('£', '').strip()
                    )
                    total_price = self._safe_float_convert(
                        total_line.replace('£', '').strip()
                    )

                    if quantity > 0:
                        result['products'].append({
                            'description': description,
                            'seller_sku': barcode,
                            'quantity': quantity,
                            'tax': 20.0,
                            'unit_price': unit_price,
                            'total_price': total_price,
                        })
                    i += 5
                    continue

            i += 1


class ConnectBeautyExtractor(BaseInvoiceExtractor):
    """Extractor for Connect Beauty (CB) invoices."""

    def convert_pdf_with_fitz(self, invoice_path: str) -> tuple[str, dict]:
        """Convert PDF and extract invoice data."""
        all_text = ''
        all_tables: list[Any] = []

        with pymupdf.open(invoice_path) as doc:
            for page in doc:
                text = page.get_text()
                all_text += text
                page_tables = page.find_tables()
                for table in page_tables:
                    all_tables.extend(table.extract())

        result = self._process_invoice_data(all_tables, all_text)
        return all_text, result

    def _process_invoice_data(self, all_tables: list, all_text: str) -> dict[str, Any]:
        result: dict[str, Any] = {'invoice_metadata': {}, 'products': []}

        self._extract_metadata(all_text, result)
        self._extract_products(all_text, result)

        return result

    def _extract_metadata(self, all_text: str, result: dict) -> None:
        # Invoice number: Invoice CB6122
        invoice_match = re.search(r'Invoice\s+(CB\d+)', all_text)
        if invoice_match:
            result['invoice_metadata']['invoice_no'] = invoice_match.group(1)

        # Order number
        order_match = re.search(r'Order Number:\s*(CB\d+)', all_text)
        if order_match:
            result['invoice_metadata']['order_no'] = order_match.group(1)

        # Issue Date: July 14, 2025
        date_match = re.search(
            r'Issue Date:\s*(\w+\s+\d+,?\s*\d{4})', all_text, re.IGNORECASE
        )
        if date_match:
            result['invoice_metadata']['invoice_date'] = date_match.group(1)

        # Total incl. VAT £654.44
        total_match = re.search(r'Total incl\. VAT\s*£([\d.,]+)', all_text)
        if total_match:
            result['invoice_metadata']['invoice_total'] = self._safe_float_convert(
                total_match.group(1)
            )

        # VAT (GB VAT) 20% £109.07
        vat_match = re.search(r'VAT\s*\([^)]+\)\s*\d+%\s*£([\d.,]+)', all_text)
        if vat_match:
            result['invoice_metadata']['total_vat_amount'] = self._safe_float_convert(
                vat_match.group(1)
            )

        # Total excl. VAT £545.37
        subtotal_match = re.search(r'Total excl\. VAT\s*£([\d.,]+)', all_text)
        if subtotal_match:
            result['invoice_metadata']['total_net_amount'] = self._safe_float_convert(
                subtotal_match.group(1)
            )

    def _extract_products(self, all_text: str, result: dict) -> None:
        """Extract products from Connect Beauty invoice.

        CB format varies:
        - Most products: description lines, then SKU: on separate line, then sku value
        - Some products: SKU: O-XXX on same line
        - Page breaks can split products mid-description
        - Some products have SKU split across pages (SKU: on page 1, value on page 2)

        Pattern: description, SKU marker, sku_value, qty, price, vat%, £total
        """
        lines = all_text.split('\n')
        lines = [ln.strip() for ln in lines]

        # First pass: find orphaned SKU values at page breaks
        # These are SKU codes that appear after page headers without SKU: marker
        orphan_skus = self._find_orphan_skus(lines)

        i = 0
        while i < len(lines):
            line = lines[i]

            # Pattern 1: "SKU:" on its own line
            if line == 'SKU:':
                # Check if next line is a valid SKU or if it's a number (page break case)
                next_line = lines[i + 1] if i + 1 < len(lines) else ''

                if next_line.isdigit():
                    # Page break case: SKU value is on next page
                    # Try to find matching orphan SKU
                    product = self._extract_split_product(lines, i, orphan_skus)
                    if product:
                        result['products'].append(product)
                    i += 5  # Skip qty, price, vat%, £total
                    continue
                else:
                    # Normal case: SKU value on next line
                    product = self._extract_product_at_sku_marker(lines, i)
                    if product:
                        result['products'].append(product)
                    i += 6
                    continue

            # Pattern 2: "SKU: O-" prefix on same line (SKU value on next line)
            if line == 'SKU: O-':
                product = self._extract_product_at_sku_marker(
                    lines, i, strip_o_prefix=True
                )
                if product:
                    result['products'].append(product)
                i += 6
                continue

            i += 1

    def _find_orphan_skus(self, lines: list) -> dict:
        """Find SKU values that appear after page break without SKU: marker.

        Returns dict mapping orphan SKU to its line index and associated description.
        """
        orphans = {}
        for i, line in enumerate(lines):
            # Look for pattern: after "Amount Paid" or page total, description, then SKU code
            if self._looks_like_sku(line):
                # Check if previous lines have description (not SKU: marker)
                if (
                    i > 0
                    and lines[i - 1] != 'SKU:'
                    and not lines[i - 1].startswith('SKU:')
                ):
                    # Check if followed by qty, price, vat%, £total (or just description)
                    if i + 1 < len(lines):
                        # Collect description going back
                        desc_lines = []
                        j = i - 1
                        while j >= 0:
                            prev = lines[j]
                            if (
                                prev.startswith('£') and 'Amount' not in lines[j - 1]
                                if j > 0
                                else True
                            ):
                                break
                            if prev in [
                                'Total excl. VAT',
                                'Total incl. VAT',
                                'VAT (GB VAT) 20%',
                                'Amount Paid',
                            ]:
                                break
                            if 'connectbeauty' in prev.lower():
                                break
                            if prev:
                                desc_lines.insert(0, prev)
                            j -= 1

                        orphans[line] = {
                            'index': i,
                            'description': ' '.join(desc_lines),
                        }
        return orphans

    def _looks_like_sku(self, text: str) -> bool:
        """Check if text looks like a CB SKU (uppercase letters + numbers)."""
        if not text or len(text) < 6:
            return False
        # SKU pattern: uppercase letters followed by numbers, e.g. NYXEYEEPI007
        return bool(re.match(r'^[A-Z]{3,}[A-Z0-9]*\d{2,}$', text))

    def _extract_split_product(
        self, lines: list, sku_marker_idx: int, orphan_skus: dict
    ) -> dict | None:
        """Extract product where SKU: is on one page but value on next page."""
        # Get description from before SKU: marker
        desc_lines = []
        j = sku_marker_idx - 1
        while j >= 0:
            desc_line = lines[j]
            if desc_line in [
                'Total',
                'VAT',
                'Unit Price',
                'Quantity',
                'Description',
                'Item',
            ]:
                break
            if desc_line.startswith('£') or desc_line.endswith('%'):
                break
            if 'connectbeauty' in desc_line.lower():
                break
            if desc_line:
                desc_lines.insert(0, desc_line)
            j -= 1

        description_part1 = ' '.join(desc_lines)

        # Data after SKU: marker (qty, price, vat%, £total)
        qty_line = lines[sku_marker_idx + 1] if sku_marker_idx + 1 < len(lines) else ''
        price_line = (
            lines[sku_marker_idx + 2] if sku_marker_idx + 2 < len(lines) else ''
        )
        vat_line = lines[sku_marker_idx + 3] if sku_marker_idx + 3 < len(lines) else ''
        total_line = (
            lines[sku_marker_idx + 4] if sku_marker_idx + 4 < len(lines) else ''
        )

        # Find matching orphan SKU (the one with description continuing this product)
        for sku, orphan_data in orphan_skus.items():
            # Check if orphan description continues our product
            orphan_desc = orphan_data['description']
            if orphan_desc:
                # Build full description
                full_description = f'{description_part1} {orphan_desc}'
                return self._build_product(
                    full_description, sku, qty_line, price_line, vat_line, total_line
                )

        return None

    def _extract_product_at_sku_marker(
        self, lines: list, sku_idx: int, strip_o_prefix: bool = False
    ) -> dict | None:
        """Extract product when SKU: is on its own line."""
        if sku_idx + 5 >= len(lines):
            return None

        # Go back to find product description
        desc_lines = []
        j = sku_idx - 1
        while j >= 0:
            desc_line = lines[j]
            # Stop at headers or previous product's total
            if desc_line in [
                'Total',
                'VAT',
                'Unit Price',
                'Quantity',
                'Description',
                'Item',
            ]:
                break
            if desc_line.startswith('£') or desc_line.endswith('%'):
                break
            # Stop at footer/header markers
            if 'connectbeauty' in desc_line.lower() or 'Connect Beauty' in desc_line:
                break
            if desc_line:
                desc_lines.insert(0, desc_line)
            j -= 1

        description = ' '.join(desc_lines)

        # SKU value on next line
        sku_line = lines[sku_idx + 1]
        sku = sku_line.lstrip('O-') if strip_o_prefix else sku_line

        # Following lines: qty, price, vat%, £total
        qty_line = lines[sku_idx + 2]
        price_line = lines[sku_idx + 3]
        vat_line = lines[sku_idx + 4]
        total_line = lines[sku_idx + 5]

        return self._build_product(
            description, sku, qty_line, price_line, vat_line, total_line
        )

    def _build_product(
        self,
        description: str,
        sku: str,
        qty_line: str,
        price_line: str,
        vat_line: str,
        total_line: str,
    ) -> dict | None:
        """Build product dict from extracted data."""
        if not qty_line.isdigit() or not vat_line.endswith('%'):
            return None

        quantity = int(qty_line)
        unit_price = self._safe_float_convert(price_line)
        vat = self._safe_float_convert(vat_line.rstrip('%'))
        total_price = self._safe_float_convert(total_line.replace('£', ''))

        if quantity > 0 and description:
            return {
                'description': description,
                'seller_sku': sku,
                'quantity': quantity,
                'tax': vat,
                'unit_price': unit_price,
                'total_price': total_price,
            }
        return None


class CherryExtractor(BaseInvoiceExtractor):
    """Extractor for Cherry Cosmetics invoices."""

    def convert_pdf_with_fitz(self, invoice_path: str) -> tuple[str, dict]:
        """Convert PDF and extract invoice data."""
        all_text = ''

        with pymupdf.open(invoice_path) as doc:
            for page in doc:
                text = page.get_text()
                all_text += text

        result = self._process_invoice_data(all_text)
        return all_text, result

    def _process_invoice_data(self, all_text: str) -> dict[str, Any]:
        result: dict[str, Any] = {'invoice_metadata': {}, 'products': []}

        self._extract_metadata(all_text, result)
        self._extract_products(all_text, result)

        return result

    def _extract_metadata(self, all_text: str, result: dict) -> None:
        # Invoice No. 44546-16531
        invoice_match = re.search(r'Invoice No\.\s*([\d-]+)', all_text)
        if invoice_match:
            result['invoice_metadata']['invoice_no'] = invoice_match.group(1)

        # Order No. 44546
        order_match = re.search(r'Order No\.\s*(\d+)', all_text)
        if order_match:
            result['invoice_metadata']['order_no'] = order_match.group(1)

        # Date: 01/11/2025
        date_match = re.search(r'Date:\s*([\d/]+)', all_text)
        if date_match:
            result['invoice_metadata']['invoice_date'] = date_match.group(1)

        # Amount: £665.32
        total_match = re.search(r'Amount:\s*£([\d.,]+)', all_text)
        if total_match:
            result['invoice_metadata']['invoice_total'] = self._safe_float_convert(
                total_match.group(1)
            )

    def _extract_products(self, all_text: str, result: dict) -> None:
        # Cherry format: Product name spans 2 lines, then Qty, Price, Total, Total inc tax
        # Each product: Name line1 \n Name line2 (with X qty) \n qty \n price \n total \n total_inc
        lines = all_text.split('\n')

        i = 0
        while i < len(lines) - 5:
            line = lines[i].strip()

            # Look for product pattern: line followed by "X N" pattern (pack size)
            next_line = lines[i + 1].strip() if i + 1 < len(lines) else ''

            # Check if next line contains pack size like "Lipstick 200 Rose Embrace X 6"
            if re.search(r'X\s*\d+$', next_line):
                # Combine product name
                description = f'{line} {next_line}'.strip()

                # Next lines should be: qty, price, total, total_inc
                qty_line = lines[i + 2].strip() if i + 2 < len(lines) else ''
                price_line = lines[i + 3].strip() if i + 3 < len(lines) else ''
                total_line = lines[i + 4].strip() if i + 4 < len(lines) else ''

                if qty_line.isdigit():
                    quantity = int(qty_line)
                    unit_price = self._safe_float_convert(
                        price_line.replace('£', '').strip()
                    )
                    total_price = self._safe_float_convert(
                        total_line.replace('£', '').strip()
                    )

                    # Extract pack size from description
                    pack_match = re.search(r'X\s*(\d+)$', description)
                    pack_size = int(pack_match.group(1)) if pack_match else 1

                    # Clean description
                    clean_desc = re.sub(r'\s*X\s*\d+$', '', description).strip()

                    if quantity > 0:
                        result['products'].append({
                            'description': clean_desc,
                            'seller_sku': None,
                            'quantity': quantity * pack_size,
                            'tax': 20.0,
                            'unit_price': unit_price / pack_size,
                            'total_price': total_price,
                        })
                    i += 6
                    continue

            i += 1


class ApolloExtractor(BaseInvoiceExtractor):
    """Extractor for Apollo Accessories invoices/orders."""

    def convert_pdf_with_fitz(self, invoice_path: str) -> tuple[str, dict]:
        """Convert PDF and extract invoice data."""
        all_text = ''

        with pymupdf.open(invoice_path) as doc:
            for page in doc:
                text = page.get_text()
                all_text += text

        result = self._process_invoice_data(all_text)
        return all_text, result

    def _process_invoice_data(self, all_text: str) -> dict[str, Any]:
        result: dict[str, Any] = {'invoice_metadata': {}, 'products': []}

        self._extract_metadata(all_text, result)
        self._extract_products(all_text, result)

        return result

    def _extract_metadata(self, all_text: str, result: dict) -> None:
        # Order # 1000089886
        order_match = re.search(r'Order\s*#\s*(\d+)', all_text)
        if order_match:
            result['invoice_metadata']['invoice_no'] = order_match.group(1)
            result['invoice_metadata']['order_no'] = order_match.group(1)

        # Complete Order Date: 9 September 2025
        date_match = re.search(
            r'Complete Order Date:\s*(\d+\s+\w+\s+\d{4})', all_text, re.IGNORECASE
        )
        if date_match:
            result['invoice_metadata']['invoice_date'] = date_match.group(1)

        # Grand Total (Incl.Tax) £292.23
        total_match = re.search(r'Grand Total \(Incl\.Tax\)\s*£([\d.,]+)', all_text)
        if total_match:
            result['invoice_metadata']['invoice_total'] = self._safe_float_convert(
                total_match.group(1)
            )

        # Tax £48.72
        vat_match = re.search(r'Tax\s*£([\d.,]+)', all_text)
        if vat_match:
            result['invoice_metadata']['total_vat_amount'] = self._safe_float_convert(
                vat_match.group(1)
            )

        # Grand Total (Excl.Tax) £243.51
        subtotal_match = re.search(r'Grand Total \(Excl\.Tax\)\s*£([\d.,]+)', all_text)
        if subtotal_match:
            result['invoice_metadata']['total_net_amount'] = self._safe_float_convert(
                subtotal_match.group(1)
            )

    def _extract_products(self, all_text: str, result: dict) -> None:
        # Apollo format: Each product is 5 lines:
        # Line 1: Product name
        # Line 2: SKU (5-6 digits)
        # Line 3: Excl. VAT: £price
        # Line 4: Quantity
        # Line 5: Excl. VAT: £total
        lines = all_text.split('\n')

        i = 0
        while i < len(lines) - 4:
            line = lines[i].strip()

            # Check if next line is a SKU (5-6 digits)
            next_line = lines[i + 1].strip() if i + 1 < len(lines) else ''

            if (
                line
                and not line.startswith('Excl.')
                and not line.startswith('£')
                and not line.startswith('Chat')
                and not line.startswith('Shipping')
                and not line.startswith('Grand')
                and not line.startswith('Subtotal')
                and not line.startswith('Product Name')
                and re.match(r'^\d{4,6}$', next_line)
            ):
                # This is a product
                description = line
                sku = next_line
                price_line = lines[i + 2].strip() if i + 2 < len(lines) else ''
                qty_line = lines[i + 3].strip() if i + 3 < len(lines) else ''
                total_line = lines[i + 4].strip() if i + 4 < len(lines) else ''

                # Parse price: "Excl. VAT: £23.40"
                price_match = re.search(r'£([\d.]+)', price_line)
                total_match = re.search(r'£([\d.]+)', total_line)

                if price_match and qty_line.isdigit() and total_match:
                    unit_price = float(price_match.group(1))
                    quantity = int(qty_line)
                    total_price = float(total_match.group(1))

                    if quantity > 0:
                        result['products'].append({
                            'description': description,
                            'seller_sku': sku,
                            'quantity': quantity,
                            'tax': 20.0,
                            'unit_price': unit_price,
                            'total_price': total_price,
                        })
                    i += 5
                    continue

            i += 1


def get_extractor_for_supplier(supplier_name: str) -> BaseInvoiceExtractor:
    """Get appropriate extractor for supplier."""
    supplier_lower = supplier_name.lower()

    if 'shure' in supplier_lower:
        return ShureExtractor()
    elif 'wts' in supplier_lower or 'wholesale trading' in supplier_lower:
        return WholesaleTradingExtractor()
    elif 'very' in supplier_lower:
        return VeryExtractor()
    elif 'connect' in supplier_lower or 'cb' in supplier_lower:
        return ConnectBeautyExtractor()
    elif 'cherry' in supplier_lower:
        return CherryExtractor()
    elif 'apollo' in supplier_lower:
        return ApolloExtractor()
    else:
        raise ValueError(f'Unknown supplier: {supplier_name}')
