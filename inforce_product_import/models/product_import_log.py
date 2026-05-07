from odoo import fields, models


class ProductImportLog(models.Model):
    """Records the result of each product import run."""

    _name = 'product.import.log'
    _description = 'Product Import Log'
    _order = 'date desc'
    _rec_name = 'file_name'

    file_name = fields.Char(string='File', readonly=True)
    date = fields.Datetime(string='Date', readonly=True, default=fields.Datetime.now)
    created = fields.Integer(string='Created', readonly=True)
    updated = fields.Integer(string='Updated', readonly=True)
    skipped = fields.Integer(string='Skipped', readonly=True)
    price_warnings = fields.Integer(string='Price Warnings', readonly=True)
    state = fields.Selection(
        [('success', 'Success'), ('warning', 'Warning')],
        string='Status',
        readonly=True,
    )
