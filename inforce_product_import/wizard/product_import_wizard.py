import base64
import io

from odoo import _, fields, models
from odoo.exceptions import UserError

# Maps xlsx column headers to internal keys
_COLUMNS = {
    'Артикул': 'sku',
    'Товар': 'name',
    'Бренд': 'brand',
    'Варіант': 'variant',
    'Одиниця вимірювання': 'uom',
    'Ціна за одиницю': 'price',
}


class ProductImportWizard(models.TransientModel):
    """Wizard for importing products from an XLSX file."""

    _name = 'product.import.wizard'
    _description = 'Product Import Wizard'

    file_data = fields.Binary(string='XLSX File', required=True)
    file_name = fields.Char(string='File Name')

    def action_import(self):
        """Read the uploaded XLSX and create/update product records."""
        try:
            import openpyxl
        except ImportError:
            raise UserError(_('The openpyxl library is not installed.'))

        if not self.file_name or not self.file_name.lower().endswith('.xlsx'):
            raise UserError(_('Please upload a valid .xlsx file.'))

        try:
            wb = openpyxl.load_workbook(
                io.BytesIO(base64.b64decode(self.file_data)),
                read_only=True,
                data_only=True,
            )
        except Exception:
            raise UserError(_('Could not read the file. Make sure it is a valid .xlsx file.'))

        rows = list(wb.active.iter_rows(values_only=True))
        if not rows:
            raise UserError(_('The file is empty.'))

        col_map = self._parse_header(rows[0])

        # Per-import caches to avoid redundant DB lookups
        brand_cache = {}
        uom_cache = {}

        created = updated = skipped = 0
        for row in rows[1:]:
            if all(v is None for v in row):
                continue
            data = {
                key: (str(row[idx]).strip() if row[idx] is not None else '')
                for key, idx in col_map.items()
            }
            if not data['sku'] or not data['name']:
                skipped += 1
                continue
            is_new = self._import_row(data, brand_cache, uom_cache)
            created += is_new
            updated += not is_new

        msg = _('%d products created, %d updated.') % (created, updated)
        if skipped:
            msg += ' ' + _('%d rows skipped (missing SKU or name).') % skipped

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Import complete'),
                'message': msg,
                'type': 'success',
                'sticky': False,
            },
        }

    def _parse_header(self, header_row):
        """Return {key: col_index} for all expected columns."""
        header = [str(c).strip() if c is not None else '' for c in header_row]
        result, missing = {}, []
        for col_name, key in _COLUMNS.items():
            try:
                result[key] = header.index(col_name)
            except ValueError:
                missing.append(col_name)
        if missing:
            raise UserError(
                _('Missing columns: %s\nFound in file: %s')
                % (', '.join(missing), ', '.join(filter(None, header)))
            )
        return result

    def _import_row(self, data, brand_cache, uom_cache):
        """Create or update a product.template from a parsed row."""
        template = self.env['product.template'].search(
            [('default_code', '=', data['sku'])], limit=1
        )
        is_new = not template

        vals = {
            'name': data['name'],
            'default_code': data['sku'],
            'list_price': self._to_float(data['price']),
        }

        if data['brand']:
            vals['brand_id'] = self._get_or_create(
                'product.brand', data['brand'], brand_cache,
                lambda n: {'name': n},
            ).id

        if data['uom']:
            uom = self._get_or_create_uom(data['uom'], uom_cache)
            vals['uom_id'] = uom.id
            vals['uom_po_id'] = uom.id

        if is_new:
            template = self.env['product.template'].create(vals)
        else:
            template.write(vals)

        if data['variant']:
            self._apply_variant(template, data['variant'])

        return is_new

    def _apply_variant(self, template, variant_str):
        """Attach an 'Attribute: Value' variant string to the product template."""
        attr_name, _, value_name = variant_str.partition(':')
        attr_name, value_name = attr_name.strip(), value_name.strip()
        if not attr_name or not value_name:
            return

        Attr = self.env['product.attribute']
        AttrVal = self.env['product.attribute.value']
        AttrLine = self.env['product.template.attribute.line']

        attribute = (
            Attr.search([('name', '=', attr_name)], limit=1)
            or Attr.create({'name': attr_name})
        )
        attr_value = (
            AttrVal.search([('attribute_id', '=', attribute.id), ('name', '=', value_name)], limit=1)
            or AttrVal.create({'attribute_id': attribute.id, 'name': value_name})
        )

        line = AttrLine.search(
            [('product_tmpl_id', '=', template.id), ('attribute_id', '=', attribute.id)],
            limit=1,
        )
        if line:
            if attr_value not in line.value_ids:
                line.write({'value_ids': [(4, attr_value.id)]})
        else:
            AttrLine.create({
                'product_tmpl_id': template.id,
                'attribute_id': attribute.id,
                'value_ids': [(4, attr_value.id)],
            })

    def _get_or_create(self, model_name, name, cache, create_vals_fn):
        """Return a cached record by name, creating it if absent."""
        if name not in cache:
            cache[name] = (
                self.env[model_name].search([('name', '=', name)], limit=1)
                or self.env[model_name].create(create_vals_fn(name))
            )
        return cache[name]

    def _get_or_create_uom(self, name, cache):
        """Return a cached uom.uom by name, creating it with its own category if absent."""
        if name not in cache:
            uom = self.env['uom.uom'].search([('name', '=', name)], limit=1)
            if not uom:
                categ = self.env['uom.category'].create({'name': name})
                uom = self.env['uom.uom'].create({
                    'name': name,
                    'category_id': categ.id,
                    'uom_type': 'reference',
                })
            cache[name] = uom
        return cache[name]

    @staticmethod
    def _to_float(value):
        """Parse a cell value to float, returning 0.0 on failure."""
        try:
            return float(str(value).replace(',', '.'))
        except (ValueError, TypeError):
            return 0.0
