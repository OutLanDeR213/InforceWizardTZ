from odoo import fields, models


class ProductImportWizard(models.TransientModel):
    """Wizard for importing products from an XLSX file."""

    _name = 'product.import.wizard'
    _description = 'Product Import Wizard'

    file_data = fields.Binary(string='XLSX File', required=True)
    file_name = fields.Char(string='File Name')

    def action_import(self):
        """Read the uploaded XLSX and create/update product records."""
        # TODO: implement in Step 2
        raise NotImplementedError('Import logic will be added in Step 2')
