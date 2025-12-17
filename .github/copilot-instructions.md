# GitHub Copilot Instructions for InvenTree Project

## Language Rules

- **Odpowiedzi dla użytkownika**: Zawsze odpowiadaj w języku **polskim**
- **Kod i komentarze**: Zawsze pisz w języku **angielskim** (docstringi, komentarze, nazwy zmiennych, komunikaty w kodzie)

## Pre-commit Rules (CRITICAL)

Before committing, always ensure code passes `pre-commit run --all-files`. Key rules:

### Python Docstrings (ruff D-codes)

1. **D415**: First line MUST end with period (`.`), question mark (`?`), or exclamation point (`!`)
   ```python
   # ❌ Wrong
   """This is a docstring"""

   # ✅ Correct
   """This is a docstring."""
   ```

2. **D205**: Multiline docstrings need blank line between summary and description
   ```python
   # ❌ Wrong
   """Summary line.
   Description continues here.
   """

   # ✅ Correct
   """Summary line.

   Description continues here.
   """
   ```

3. **D101, D102, D103, D107**: All classes and methods need docstrings
   - D101: Missing docstring in public class
   - D102: Missing docstring in public method
   - D103: Missing docstring in public function
   - D107: Missing docstring in `__init__`

### Unused Variables (ruff B007, F841, RUF059)

- Prefix unused variables with underscore: `_variable`
- Or remove the assignment entirely if not needed

```python
# ❌ Wrong
for item in items:
    pass

created = SomeModel.objects.create(...)  # if 'created' not used

# ✅ Correct
for _item in items:
    pass

SomeModel.objects.create(...)  # remove assignment if not used
_created = SomeModel.objects.create(...)  # or prefix with underscore
```

### zip() Function (ruff B905)

Always use `strict=` parameter with `zip()`:

```python
# ❌ Wrong
for a, b in zip(list1, list2):
    pass

# ✅ Correct
for a, b in zip(list1, list2, strict=True):
    pass

# Or if lengths may differ intentionally:
for a, b in zip(list1, list2, strict=False):
    pass
```

### Django Templates (djlint T003)

`{% endblock %}` tags must include block name:

```html
<!-- ❌ Wrong -->
{% block body %}
...
{% endblock %}

<!-- ✅ Correct -->
{% block body %}
...
{% endblock body %}
```

### File Encoding (ruff PLW1514)

Always specify encoding for file operations:

```python
# ❌ Wrong
with tempfile.NamedTemporaryFile(mode='w', suffix='.csv') as tmp:

# ✅ Correct
with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', encoding='utf-8') as tmp:
```

## Codespell Exclusions

The following directories are excluded from codespell (to preserve supplier names like "Shure"):
- `src/backend/InvenTree/invoice_manager/.*`
- `src/backend/InvenTree/importer/.*`

## Project Structure

Key custom modules added to InvenTree:

### invoice_manager
- Location: `src/backend/InvenTree/invoice_manager/`
- Purpose: PDF invoice parsing, Part matching, Purchase Order creation
- Features: Multi-extractor support (TME, generic), admin interface for matching parts

### importer
- Location: `src/backend/InvenTree/importer/`
- Purpose: Import supplier products from CSV files
- Features: Deduplication by barcode, auto-create Parts and SupplierParts

### Custom Admin
- Location: `src/backend/InvenTree/InvenTree/custom_admin.py`
- Purpose: Extended Django admin with import functionality

## Frontend Changes

### Receive All Feature
- Backend: `order/api.py` - `PurchaseOrderReceiveAll` endpoint
- Backend: `order/serializers.py` - `PurchaseOrderReceiveAllSerializer`
- Frontend: `src/frontend/src/pages/purchasing/PurchaseOrderDetail.tsx`
- API Endpoint: `ApiEndpoints.purchase_order_receive_all`

**Important**: Don't use `t\`...\`` template literals for button text if translation isn't configured - use plain strings.

## Running Pre-commit

```bash
cd /home/inventree
git add -A
pre-commit run --all-files
```

## Common Tasks

```bash
# Run development server
invoke dev.server

# Run migrations
invoke migrate

# Run tests
invoke dev.test

# Setup dev environment
invoke dev.setup-dev
```
