from odoo import api, models


class PaymentProvider(models.Model):
    _inherit = 'payment.provider'

    @api.model
    def _get_compatible_providers(
        self,
        company_id,
        partner_id,
        amount,
        currency_id=None,
        **kwargs,
    ):
        providers = super()._get_compatible_providers(
            company_id,
            partner_id,
            amount,
            currency_id=currency_id,
            **kwargs,
        )

        sale_order_id = kwargs.get('sale_order_id')

        if not sale_order_id:
            sale_order_id = self.env.context.get('sale_order_id')

        if not sale_order_id:
            return providers

        order = self.env['sale.order'].browse(
            int(sale_order_id)
        ).exists()

        if not order:
            return providers

        carrier = order.carrier_id

        if (
            carrier
            and carrier.delivery_type == 'redbox'
            and carrier.blocked_payment_provider_ids
        ):
            providers -= carrier.blocked_payment_provider_ids

        return providers