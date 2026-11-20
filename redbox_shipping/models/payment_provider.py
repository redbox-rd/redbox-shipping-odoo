from odoo import api, models


class PaymentProvider(models.Model):
    _inherit = 'payment.provider'

    @api.model
    def _get_compatible_providers(self, *args, **kwargs):
        # Signature differs between Odoo 17/18/19: pass everything through untouched.
        providers = super()._get_compatible_providers(*args, **kwargs)
        sale_order_id = kwargs.get('sale_order_id') or self.env.context.get('sale_order_id')
        return self._redbox_filter_providers(providers, sale_order_id)

    @api.model
    def _redbox_filter_providers(self, providers, sale_order_id):
        if not sale_order_id:
            return providers
        order = self.env['sale.order'].sudo().browse(int(sale_order_id)).exists()
        carrier = order.carrier_id
        if carrier and carrier.delivery_type == 'redbox':
            providers -= carrier._redbox_get_blocked_providers()
        return providers
