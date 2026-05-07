{
    'name': 'Product Import from XLSX',
    'version': '18.0.1.0.0',
    'summary': 'Import products from an XLSX file using a wizard',
    'author': 'Inforce',
    'depends': ['product', 'uom', 'stock'],
    'data': [
        'security/ir.model.access.csv',
        'views/product_brand_views.xml',
        'views/product_template_views.xml',
        'views/product_import_wizard_views.xml',
    ],
    'installable': True,
    'license': 'LGPL-3',
}
