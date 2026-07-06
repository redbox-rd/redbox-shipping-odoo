from odoo import models, api
from odoo.http import request

class PaymentProvider(models.Model):
    _inherit = 'payment.provider' # use 'payment.acquirer' if using Odoo 14 or earlier

    @api.model
    def _get_compatible_providers(self, company_id, partner_id, amount, currency_id=None, **kwargs):
        # 1. Get the default list of payment providers
        providers = super()._get_compatible_providers(
            company_id, partner_id, amount, currency_id=currency_id, **kwargs
        )
        
        # 2. Get the current sale order information
        order = self.env['sale.order']
        sale_order_id = kwargs.get('sale_order_id')
        if sale_order_id:
            order = self.env['sale.order'].browse(sale_order_id)
        elif request and hasattr(request, 'website'):
            order = request.website.sale_get_order()
            
        # 3. If the current order's shipping method is Redbox
        if order and order.carrier_id and order.carrier_id.delivery_type == 'redbox':
            # Get the blocked payment providers configured ON REDBOX
            blocked_providers = order.carrier_id.blocked_payment_provider_ids
            if blocked_providers:
                # Exclude the blocked providers from the displayed list
                providers = providers - blocked_providers
                
        return providers