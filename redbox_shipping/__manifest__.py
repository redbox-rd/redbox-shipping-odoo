{
    'name': 'Redbox Shipping',
    'version': '1.0',
    'summary': 'Shipping integration with Redbox',
    'description': """
    <h2>Redbox Shipping Integration</h2>

    <p>RedBox offers innovative delivery service via lockers for a high-speed and low-cost shopping experience.</p>

    <p>Merchants enjoy same-day or next-day delivery within the same city and 2–3 days to other cities.</p>

    <p>Our self-deposit service empowers merchants to deposit shipments directly into lockers via API integration.</p>

    <p>Customers can receive, return, and track shipments 24/7. With over 2000 RedBox Points, delivery becomes flexible and convenient.</p>

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
    <h3>Preview</h3>
    <img src="banner.png"/>
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