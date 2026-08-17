from odoo import models, fields, api, _
from odoo.exceptions import UserError
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
    redbox_fixed_price = fields.Float(
        string="Redbox Fixed Price", 
        default=12.0,
        help="Enter a fixed price for Redbox shipping"
    )
    blocked_payment_provider_ids = fields.Many2many(
        'payment.provider', # use 'payment.acquirer' if using Odoo 14 or earlier
        'delivery_carrier_payment_provider_rel', # Name of the intermediary table
        'carrier_id', 'provider_id',
        string="Blocked Payment Methods",
        help="Select payment methods to BLOCK when the customer chooses Redbox shipping."
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
        Return the carrier's configured price without overriding it.
        """
        return {
            'success': True,
            'price': self.redbox_fixed_price,
            'error_message': False,
            'warning_message': False,
        }

    def redbox_send_shipping(self, pickings):
        """
        Send shipment to Redbox API and create a shipping label.
        Called by Odoo's delivery framework when 'Send to Shipper' is pressed.
        """
        result = []
        for picking in pickings:
            self = picking.carrier_id
            self.ensure_one()

            if not self.redbox_api_key:
                raise ValueError(_("Redbox API key is not configured for carrier %s") % self.name)

            # Build items from the picking's sale order (mirrors _create_redbox_shipment)
            order = picking.sale_id
            items = []
            if order:
                for line in order.order_line:
                    if line.is_delivery:
                        continue
                    items.append({
                        "name": line.product_id.name,
                        "quantity": line.product_uom_qty,
                        "unit_price": line.price_unit,
                    })

            # Use the shipping partner from the sale order
            shipping_partner = order.partner_shipping_id if order else picking.partner_id

            payload = {
                "reference": order.name if order else picking.name,
                "customer_name": shipping_partner.name,
                "cod_amount": order.amount_to_pay if order else 0.0,
                "cod_currency": order.currency_id.name if order else "SAR",
                "customer_phone": shipping_partner.phone or "",
                "customer_address": shipping_partner.contact_address or "",
                "customer_city": shipping_partner.state_id.name if shipping_partner.state_id else "",
                "customer_country": shipping_partner.country_id.name if shipping_partner.country_id else "",
                "items": items,
            }

            _logger.info("=== REDBOX SEND SHIPPING ===")
            _logger.info("Payload: %s", payload)

            try:
                response = requests.post(
                    "https://api.redboxsa.com/v3/shipments",
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {self.redbox_api_key}"
                    },
                    timeout=50
                )
                _logger.info("Response Status: %s", response.status_code)
                _logger.info("Response: %s", response.text)
                response.raise_for_status()
                data = response.json()
            except Exception as e:
                _logger.error("Redbox send shipping failed for picking %s: %s", picking.name, e)
                raise UserError(_("Redbox shipping failed: %s") % str(e))

            # Handle response (mirrors _create_redbox_shipment / _handle_redbox_response)
            if data.get('success'):
                _logger.info(
                    "REDBOX shipment created successfully for order %s. Tracking: %s",
                    order.name if order else picking.name,
                    data.get('tracking_number')
                )
                picking.write({
                    'carrier_tracking_ref': data.get('tracking_number'),
                    'redbox_label_url': data.get('shipping_label_url'),
                    'redbox_shipment_status': 'Pending',
                })
                result.append({
                    'exact_price': picking.carrier_price,
                    'tracking_number': data.get('tracking_number'),
                })
            else:
                error_msg = data.get('msg', 'Unknown error')
                picking.message_post(body=_('Redbox API Error: %s') % error_msg)
                _logger.error("REDBOX API Error for order %s: %s",
                              order.name if order else picking.name, error_msg)
                # Return a result with failure info so Odoo doesn't break
                result.append({
                    'exact_price': picking.carrier_price,
                    'tracking_number': False,
                })

        return result

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