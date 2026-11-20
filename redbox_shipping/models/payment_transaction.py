from odoo import _, api, models
from odoo.exceptions import ValidationError


class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    @api.model_create_multi
    def create(self, vals_list):
        transactions = super().create(vals_list)
        transactions._redbox_check_blocked_provider()
        return transactions

    def _redbox_check_blocked_provider(self):
        """Server-side guard: the payment form may have been rendered before the customer
        switched to a RedBox carrier (e.g. delivery selection on the payment page in
        Odoo 17), so hiding providers alone is not enough."""
        for tx in self.sudo():
            for order in tx.sale_order_ids:
                carrier = order.carrier_id
                if carrier.delivery_type != 'redbox':
                    continue
                if tx.provider_id in carrier._redbox_get_blocked_providers():
                    raise ValidationError(_(
                        "The payment method %(provider)s is not available with %(carrier)s "
                        "shipping. Please choose another payment method.",
                        provider=tx.provider_id.name, carrier=carrier.name))
