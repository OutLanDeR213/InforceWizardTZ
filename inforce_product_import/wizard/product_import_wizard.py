import base64
import io
import logging

from markupsafe import Markup, escape

from odoo import _, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Maps xlsx column headers to internal keys
_COLUMNS = {
    'Артикул': 'sku',
    'Товар': 'name',
    'Бренд': 'brand',
    'Варіант': 'variant',
    'Одиниця вимірювання': 'uom',
    'Ціна за одиницю': 'price',
}

_PREVIEW_LIMIT = 50


class ProductImportWizard(models.TransientModel):
    """Wizard for importing products from an XLSX file."""

    _name = 'product.import.wizard'
    _description = 'Product Import Wizard'

    state = fields.Selection(
        [('upload', 'Upload'), ('preview', 'Preview')],
        default='upload',
    )
    file_data = fields.Binary(string='XLSX File')
    file_name = fields.Char(string='File Name')
    preview_html = fields.Html(readonly=True, sanitize=False)

    def action_preview(self):
        """Parse the file, build a preview table, and switch to preview state."""
        if not self.file_data:
            raise UserError(_('Please attach a file first.'))
        if not self.file_name or not self.file_name.lower().endswith('.xlsx'):
            raise UserError(_('Please upload a valid .xlsx file.'))

        data_rows = self._load_rows()
        existing_skus = set(
            self.env['product.template']
            .search([('default_code', 'in', [d['sku'] for d in data_rows])])
            .mapped('default_code')
        )

        new_count = sum(1 for d in data_rows if d['sku'] not in existing_skus)
        update_count = len(data_rows) - new_count
        shown = data_rows[:_PREVIEW_LIMIT]

        rows_html = Markup('').join(
            Markup(
                '<tr>'
                '<td>{sku}</td><td>{name}</td><td>{brand}</td>'
                '<td>{price}</td>'
                '<td style="color:{color};font-weight:bold">{status}</td>'
                '</tr>'
            ).format(
                sku=escape(d['sku']),
                name=escape(d['name']),
                brand=escape(d['brand']),
                price=escape(d['price']),
                color='#e67e00' if d['sku'] in existing_skus else '#28a745',
                status=_('Update') if d['sku'] in existing_skus else _('New'),
            )
            for d in shown
        )

        extra = (
            Markup('<p><i>... and {} more rows not shown.</i></p>').format(len(data_rows) - _PREVIEW_LIMIT)
            if len(data_rows) > _PREVIEW_LIMIT else Markup('')
        )

        self.preview_html = Markup('''
            <p><b>{summary}</b></p>
            <table class="table table-sm table-bordered">
                <thead class="table-light">
                    <tr>
                        <th>SKU</th><th>Name</th><th>Brand</th>
                        <th>Price</th><th>Action</th>
                    </tr>
                </thead>
                <tbody>{rows}</tbody>
            </table>
            {extra}
        ''').format(
            summary=_('%d rows — %d new, %d to update') % (len(data_rows), new_count, update_count),
            rows=rows_html,
            extra=extra,
        )
        self.state = 'preview'
        return self._reopen()

    def action_back(self):
        """Return to the upload step."""
        self.state = 'upload'
        self.preview_html = False
        return self._reopen()

    def action_import(self):
        """Read the uploaded XLSX and create/update product records."""
        data_rows = self._load_rows()

        brand_cache = {}
        uom_cache = {}
        attr_cache = {}
        attr_val_cache = {}

        created = updated = price_warnings = 0
        for data in data_rows:
            price = self._to_float(data['price'])
            if price <= 0:
                price_warnings += 1
                _logger.warning('Product %s (SKU %s): price is %s', data['name'], data['sku'], price)

            is_new = self._import_row(data, brand_cache, uom_cache, attr_cache, attr_val_cache)
            created += is_new
            updated += not is_new
            _logger.info(
                '%s product %s (SKU %s)',
                'Created' if is_new else 'Updated',
                data['name'],
                data['sku'],
            )

        _logger.info(
            'Import finished: %d created, %d updated, %d price warnings',
            created, updated, price_warnings,
        )

        msg = _('%d products created, %d updated.') % (created, updated)
        if price_warnings:
            msg += ' ' + _('%d products have zero or negative price.') % price_warnings

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Import complete'),
                'message': msg,
                'type': 'success' if not price_warnings else 'warning',
                'sticky': bool(price_warnings),
            },
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _reopen(self):
        """Return an action that refreshes this wizard dialog."""
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def _load_rows(self):
        """Parse the uploaded file and return a list of validated row dicts."""
        try:
            import openpyxl
        except ImportError:
            raise UserError(_('The openpyxl library is not installed.'))

        try:
            wb = openpyxl.load_workbook(
                io.BytesIO(base64.b64decode(self.file_data)),
                read_only=True,
                data_only=True,
            )
        except Exception:
            raise UserError(_('Could not read the file. Make sure it is a valid .xlsx file.'))

        sheet = wb.active

        rows = list(sheet.iter_rows(values_only=True))
        if not rows:
            raise UserError(_('The selected sheet is empty.'))

        col_map = self._parse_header(rows[0])
        data_rows = []
        skipped = 0
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
            data_rows.append(data)

        if skipped:
            _logger.warning('%d rows skipped due to missing SKU or name.', skipped)

        return data_rows

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

    def _import_row(self, data, brand_cache, uom_cache, attr_cache, attr_val_cache):
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
            self._apply_variant(template, data['variant'], attr_cache, attr_val_cache)

        return is_new

    def _apply_variant(self, template, variant_str, attr_cache, attr_val_cache):
        """Attach an 'Attribute: Value' variant string to the product template."""
        attr_name, _, value_name = variant_str.partition(':')
        attr_name, value_name = attr_name.strip(), value_name.strip()
        if not attr_name or not value_name:
            return

        Attr = self.env['product.attribute']
        AttrVal = self.env['product.attribute.value']
        AttrLine = self.env['product.template.attribute.line']

        if attr_name not in attr_cache:
            attr_cache[attr_name] = (
                Attr.search([('name', '=', attr_name)], limit=1)
                or Attr.create({'name': attr_name})
            )
        attribute = attr_cache[attr_name]

        val_key = (attribute.id, value_name)
        if val_key not in attr_val_cache:
            attr_val_cache[val_key] = (
                AttrVal.search([('attribute_id', '=', attribute.id), ('name', '=', value_name)], limit=1)
                or AttrVal.create({'attribute_id': attribute.id, 'name': value_name})
            )
        attr_value = attr_val_cache[val_key]

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
