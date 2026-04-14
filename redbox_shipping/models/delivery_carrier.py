from odoo import models, fields, api
import logging
import requests

_logger = logging.getLogger(__name__)


class DeliveryCarrier(models.Model):
    """
    Inherit delivery.carrier to add Redbox shipping integration.
    """
    _inherit = 'delivery.carrier'

    delivery_type = fields.Selection(
        selection_add=[('redbox', 'Redbox')],
        ondelete={'redbox': 'set default'},
    )
    redbox_api_key = fields.Char(
        string="Redbox API Key",
        groups="base.group_system",
        help="API key for Redbox integration. Only visible to administrators."
    )
    redbox_webhook_created = fields.Boolean(
        string="Redbox Connected",
        default=False,
        help="Indicates if the Redbox webhook has been registered."
    )

    def get_tracking_link(self, picking):
        """
        Override to provide Redbox tracking link.
        """
        self.ensure_one()
        if self.delivery_type == 'redbox':
            if picking.carrier_tracking_ref:
                return (
                    f"https://redboxsa.com/ar/collect/parcel-tracking?tracking_number="
                    f"{picking.carrier_tracking_ref}"
                )
            return ''
        return super().get_tracking_link(picking)

    def redbox_rate_shipment(self, order):
        """
        Dummy rate shipment for Redbox (to be implemented).
        """
        return {
            'success': True,
            'price': 12,
            'error_message': False,
            'warning_message': False,
        }

    def _notify_redbox(self):
        """
        Register webhook with Redbox if not already registered.
        """
        self.ensure_one()
        if not self.redbox_api_key:
            _logger.warning("Redbox API key not set for carrier %s", self.name)
            return
        if self.redbox_webhook_created:
            _logger.warning("Redbox webhook already created for carrier %s", self.name)
            return
        try:
            base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
            webhook_url = f"{base_url}/redbox/webhook"
            payload = {
                "event": "shipment.status.update",
                "original_id": self.env.cr.dbname,
                "subscriber": self.env.cr.dbname,
                "target_url": webhook_url,
            }
            _logger.info("Registering Redbox webhook: %s", payload)
            res = requests.post(
                "https://api.redboxsa.com/v3/webhooks",
                json=payload,
                headers={
                    "Authorization": f"Bearer {self.redbox_api_key}"
                },
                timeout=5
            )
            data = res.json()
            _logger.info("Redbox webhook registration response: %s", data)
            if data.get('success'):
                _logger.info("Redbox webhook registered successfully: %s", data)
                self.write({'redbox_webhook_created': True})
            else:
                _logger.error("Redbox webhook registration failed: %s", data.get('msg'))
        except Exception as e:
            _logger.error("Registering Redbox webhook failed: %s", e)

    @api.model
    def create(self, vals_list):
        """
        Override create to notify Redbox if API key is set.
        """
        records = super().create(vals_list)

        for rec in records:
            if rec.redbox_api_key:
                rec._notify_redbox()

        return records

    def write(self, vals):
        """
        Override write to notify Redbox if API key is updated.
        """
        res = super().write(vals)
        if 'redbox_api_key' in vals:
            for rec in self:
                rec._notify_redbox()
        return res

    @api.onchange('delivery_type')
    def _onchange_delivery_type_redbox(self):
        """
        Onchange: Set name, country, and states for Redbox delivery type.
        """
        if self.delivery_type == 'redbox':
            self.name = "Redbox"
            sa = self.env.ref('base.sa', raise_if_not_found=False)
            if sa:
                self.country_ids = [(6, 0, [sa.id])]
                states = self.env['res.country.state'].search([
                    ('country_id', '=', sa.id)
                ])
                self.state_ids = [(6, 0, states.ids)]