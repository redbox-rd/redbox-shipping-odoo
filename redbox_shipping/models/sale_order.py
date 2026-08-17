import logging
import requests
import traceback
from odoo import models, _

_logger = logging.getLogger(__name__)

class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def action_confirm(self):
        """
        Override to create Redbox shipment if delivery_type is 'redbox'.
        """
        _logger.info("action_confirm called from:\n%s", "".join(traceback.format_stack()))
        _logger.info("action_confirm called: %s", self.ids)
        res = super().action_confirm()
        for order in self:
            _logger.info("action_confirm delivery_type: %s", order.carrier_id.delivery_type)
            if order.carrier_id.delivery_type == 'redbox':
                order._create_redbox_shipment()
        return res

    def _create_redbox_shipment(self):
        """
        Create a Redbox shipment for the order.
        """
        self.ensure_one()
        _logger.info("REDBOX _create_redbox_shipment called: %s", self.name)
        items = []
        for line in self.order_line:
            if line.is_delivery:
                continue

            items.append({
                "name": line.product_id.name,
                "quantity": line.product_uom_qty,
                "unit_price": line.price_unit,
            })
        payload = {
            "reference": self.name,
            "customer_name": self.partner_shipping_id.name,
            "cod_amount": self.amount_to_pay,
            "cod_currency": self.currency_id.name,
            "customer_phone": self.partner_shipping_id.phone,
            "customer_address": self.partner_shipping_id.contact_address,
            "customer_city": self.partner_shipping_id.state_id.name,
            "customer_country": self.partner_shipping_id.country_id.name,
            "items": items,
        }

        _logger.info("REDBOX Payload for order %s: %s", self.name, payload)
        try:
            response = requests.post(
                "https://api.redboxsa.com/v3/shipments",
                json=payload,
                headers={
                    "Authorization": f"Bearer {self.carrier_id.redbox_api_key}"
                },
                timeout=50
            )
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            self.message_post(body=_('Redbox API Connection Error: %s') % e)
            _logger.error("Redbox API Connection Error for order %s: %s", self.name, e)
            return

        if data.get('success'):
            _logger.info("REDBOX shipment created successfully for order %s. Tracking: %s", self.name, data.get('tracking_number'))
            self._handle_redbox_response(data)
        else:
            self.message_post(body=_('Redbox API Error: %s') % data.get('msg'))
            _logger.error("Redbox API Error for order %s: %s", self.name, data.get('msg'))

    def _handle_redbox_response(self, data):
        """
        Handle the Redbox API response and update picking.
        """
        self.ensure_one()
        picking = self.picking_ids.filtered(lambda p: p.state != 'cancel')[:1]
        tracking = data.get('tracking_number')
        label_url = data.get('shipping_label_url')
        _logger.info("REDBOX Handling Redbox response for order %s. Tracking: %s, Label URL: %s", self.name, tracking, label_url)
        if picking:
            picking.sudo().write({
                'carrier_tracking_ref': tracking,
                'redbox_label_url': label_url,
                'redbox_shipment_status': 'Pending',
            })
        else:
            self.message_post(body=_('Redbox shipment created but no picking found. Tracking: %s') % tracking)
            _logger.warning("REDBOX Redbox shipment created but no picking found for order %s. Tracking: %s", self.name, tracking)
            
    def write(self, vals):
        tracked_fields = {'order_line', 'amount_total', 'carrier_id'}

        old_data = {}
        for order in self:
            if order.carrier_id.delivery_type == 'redbox':
                old_data[order.id] = {
                    'amount_total': order.amount_total,
                    'lines': [(l.product_id.id, l.product_uom_qty, l.price_unit) for l in order.order_line],
                }
        _logger.info("write called for orders: %s, old_data: %s", self.ids, old_data)
        res = super().write(vals)

        for order in self:
            if order.carrier_id.delivery_type != 'redbox':
                continue

            picking = order.picking_ids.filtered(lambda p: p.carrier_tracking_ref)[:1]
            if not picking:
                continue

            changed = False

            old = old_data.get(order.id, {})
            new_lines = [(l.product_id.id, l.product_uom_qty, l.price_unit) for l in order.order_line]

            # check items change
            if old.get('lines') != new_lines:
                changed = True

            # check COD / amount change
            if old.get('amount_total') != order.amount_total:
                changed = True

            if not changed:
                continue

            try:
                _logger.warning("🚚 Updating Redbox shipment for order %s", order.name)
                picking._update_redbox_shipment()
            except Exception as e:
                _logger.exception("❌ Redbox update failed for order %s", order.name)
                order.message_post(body=_("Redbox update failed: %s") % str(e))

        return res
    