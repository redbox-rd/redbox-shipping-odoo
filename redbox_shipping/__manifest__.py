{
    'name': 'Redbox Shipping',
    'version': '1.0',
    'summary': 'Shipping integration with Redbox',
    'description': 'Connect Odoo with Redbox shipping system',
    'author': 'RedBox Technologies',
    'website': 'https://redboxsa.com',
    'category': 'Inventory/Delivery',
    'depends': ['delivery', 'website_sale', 'stock'],
    'data': [
        'views/delivery_views.xml',
        'views/stock_picking_views.xml',
    ],
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}