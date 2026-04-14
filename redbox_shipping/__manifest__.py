{
    'name': 'Redbox Shipping',
    'version': '1.0',
    'summary': 'Shipping integration with Redbox',
    'description': """
    <h2>Redbox Shipping Integration</h2>

    <p>RedBox offers innovative delivery service via lockers for high-speed & low-cost shopping experience
Merchants enjoy same-day or next-day delivery within the same city and 2-3 days to other cities. Our self-deposit service empowers merchants to deposit their shipments directly into the locker. Connect seamlessly to our system via API integration. Customers can receive, return, and track shipments 24/7, ensuring flexibility that fits their schedule. With over 2000 RedBox Points across many cities in the Kingdom, we're here to make the delivery experience as smooth as possible.</p>

    <h3>Features</h3>
    <ul>
        <li>Create shipment from Sale Order</li>
        <li>Cancel shipment automatically</li>
        <li>Sync tracking number</li>
    </ul>

    <h3>How to use</h3>
    <ol>
        <li>Install the module</li>
        <li>Configure API key</li>
        <li>Create Sale Order</li>
    </ol>
    """,
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