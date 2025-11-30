"""
Invoice Manager - System zarządzania fakturami w InvenTree

Integruje czytniki PDF dla różnych dostawców, automat ycznie mapuje produkty
na Parts i umożliwia dodawanie stock'u poprzez Django Admin.

USAGE:
------

1. UPLOAD FAKTURY W ADMIN
   - Wejdź na: /admin/invoice_manager/invoice/
   - Kliknij "Add Invoice"
   - Załaduj plik PDF, podaj numer faktury, datę i dostawcę

2. EKSTRAKCJA DANYCH
   - W liście faktur zaznacz fakturę
   - Wybierz "Extract invoice data from PDF" z action menu
   - System automatycznie wyodrębni:
     * Invoice metadata (numer, data, totals)
     * Listę produktów z ilościami i cenami
     * Tworzy InvoiceItem dla każdej linii

3. MATCHING PRODUKTÓW
   - Po ekstrakcji każdy InvoiceItem jest "unmatched"
   - Wybierz "Auto-match parts (by description)" aby spróbować automatycznego matchowania:
     * Najpierw próbuje po SKU (jeśli dostawca wysyła SKU)
     * Potem próbuje po słowach kluczowych z opisu
   - Dla niezmatchowanych ręcznie kliknij na InvoiceItem w inline admin
   - Wybierz odpowiadający Part z dropdown
   - Zaznacz "matched" checkbox

4. TWORZENIE STOCK'U
   - Jak wszystkie produkty są matchowane (matched=True)
   - Wybierz "Create stock entries for matched items"
   - System tworzy StockItem dla każdego produktu z:
     * Ilością z faktury
     * Notes: "Added from invoice {numer}"
     * Batch: numer faktury

SUPPORTED SUPPLIERS:
-------------------

1. SHURE COSMETICS
   - Format: PDF z tabelami
   - Zawiera: SKU, Product, Price, Quantity, Total
   - Używa: SKU do matchowania
   - Currency: GBP (£)

2. WHOLESALE TRADING SUPPLIES (WTS)
   - Format: PDF z blkami tekstu
   - Parsuje: Case#, Description, Unit Price, Net, VAT, Quantity
   - Currency: EUR (€)
   - SKU: Auto-generowany z opisu

ADDING NEW SUPPLIERS:
--------------------

1. Rozszerz invoice_manager/extractors.py:

   class MySupplierExtractor(BaseInvoiceExtractor):
       def convert_pdf_with_fitz(self, invoice_path: str) -> tuple[str, dict]:
           # Parse PDF
           raw_text, all_blocks = self._extract_pdf_content(invoice_path)

           # Extract structure
           result = {
               "invoice_metadata": {
                   "invoice_no": ...,
                   "invoice_date": ...,
                   "invoice_total": ...,
               },
               "products": [
                   {
                       "description": ...,
                       "seller_sku": ...,
                       "quantity": ...,
                       "unit_price": ...,
                       "total_price": ...,
                       "tax": ...
                   }
               ]
           }
           return raw_text, result

2. Zaktualizuj get_extractor_for_supplier():

   def get_extractor_for_supplier(supplier_name: str) -> BaseInvoiceExtractor:
       supplier_lower = supplier_name.lower()
       if "mysupplier" in supplier_lower:
           return MySupplierExtractor()
       ...

3. Add "My Supplier" as Company in InvenTree admin

4. Test z faktury w Django admin

MANAGEMENT COMMAND:
------------------

python manage.py process_invoices [options]

Options:
  --invoice-id ID      - Process specific invoice by ID
  --auto-match         - Automatically match parts after extraction
  --create-stock       - Create stock entries for matched items
  --pending-only        - Process only pending invoices (default: True)

Example:
  python manage.py process_invoices --auto-match --create-stock

DATABASE MODELS:
---------------

Invoice
  - invoice_number (unique)
  - invoice_date
  - supplier (ForeignKey: Company)
  - uploaded_by (ForeignKey: User)
  - invoice_file (PDF)
  - invoice_data (JSONField - raw extracted data)
  - total_net_amount, total_vat_amount, invoice_total
  - status (PENDING, PROCESSING, COMPLETED, FAILED, CANCELLED)
  - processing_log, error_message
  - created_at, updated_at, processed_at

InvoiceItem
  - invoice (ForeignKey: Invoice)
  - part (ForeignKey: Part, nullable)
  - description, seller_sku, quantity
  - unit_price, total_price, tax_rate
  - matched (Boolean)
  - notes

InvoiceProcessingLog
  - invoice (ForeignKey: Invoice)
  - action (UPLOAD, EXTRACT, MATCH, STOCK_CREATE, STOCK_ERROR, ERROR)
  - message
  - created_at

ADMIN FEATURES:
--------------

✓ Upload PDF invoices
✓ Auto-extract invoice data (tables + text)
✓ Display extracted data in JSON
✓ Inline InvoiceItem editing for part selection
✓ Admin actions:
  - extract_invoice_data
  - auto_match_parts
  - create_stock_entries
  - reset_invoice
✓ Processing logs and error tracking
✓ Status badges with color coding

API PROGRAMMATIC USAGE:
---------------------

from invoice_manager.models import Invoice, InvoiceItem
from invoice_manager.extractors import get_extractor_for_supplier
from part.models import Part

# Create invoice
invoice = Invoice.objects.create(
    invoice_number="INV-123",
    invoice_date="2025-11-29",
    supplier_id=1,  # Company ID for Shure
    uploaded_by_id=1,  # User ID
    invoice_file="path/to/file.pdf"
)

# Extract data
extractor = get_extractor_for_supplier("Shure Cosmetics")
raw_text, extracted_data = extractor.convert_pdf_with_fitz(invoice.invoice_file.path)

# Update invoice
invoice.invoice_data = extracted_data
invoice.total_net_amount = extracted_data["invoice_metadata"]["total_net_amount"]
invoice.save()

# Create items
for product in extracted_data["products"]:
    item = InvoiceItem.objects.create(
        invoice=invoice,
        description=product["description"],
        seller_sku=product["seller_sku"],
        quantity=product["quantity"],
        unit_price=product["unit_price"],
        total_price=product["total_price"],
        tax_rate=product["tax"]
    )

# Match part (optional)
part = Part.objects.get(name__icontains="Brut")
item.match_part(part)

# Create stock entry
stock_item = item.create_stock_entry()
"""

print(__doc__)
