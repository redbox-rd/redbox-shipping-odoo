{
    'name': 'Redbox Shipping',
    'version': '1.0',
    'summary': 'Shipping integration with Redbox',
    'author': 'RedBox Technologies',
    'website': 'https://redboxsa.com',
    'category': 'Inventory/Delivery',
    'depends': ['delivery', 'website_sale', 'payment', 'stock'],
    'data': [
        'views/delivery_views.xml',
        'views/stock_picking_views.xml',
    ],
    'images': ['static/description/banner.png'],
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}