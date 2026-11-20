{
    'name': 'RedBox Shipping',
    # Odoo prefixes the series automatically (17.0.2.0.0 / 18.0.2.0.0 / 19.0.2.0.0).
    'version': '2.0.0',
    'summary': 'Shipping integration with RedBox (lockers, home delivery, returns)',
    'author': 'RedBox Technologies',
    'website': 'https://redboxsa.com',
    'category': 'Inventory/Delivery',
    'depends': ['stock_delivery', 'website_sale', 'payment'],
    'external_dependencies': {'python': ['requests']},
    'data': [
        'views/delivery_views.xml',
        'views/stock_picking_views.xml',
    ],
    'images': ['static/description/banner.png'],
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}
