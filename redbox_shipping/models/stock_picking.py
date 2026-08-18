from odoo import models, fields, _
import logging, json, requests

_logger = logging.getLogger(__name__)


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    redbox_label_url = fields.Char(string="Shipping Label URL")
    redbox_shipment_status = fields.Char(string="Redbox Shipment Status")

    def action_open_redbox_label(self):
        """
        Open the shipping label URL in a new tab.
        """
        self.ensure_one()
        return {
            'type': 'ir.actions.act_url',
            'url': self.redbox_label_url,
            'target': 'new',
        }
    def write(self, vals):
        _logger.warning("WRITE StockPicking: %s", vals.keys())
        watch_fields = ['move_ids','move_ids_without_package']

        old_data = {}
        _logger.warning("WRITE StockPicking old_data: %s", old_data)
        if any(f in vals for f in watch_fields):
            for picking in self:
                if picking.carrier_id.delivery_type == 'redbox':
                    old_data[picking.id] = picking._get_redbox_items_json()
        _logger.warning("WRITE StockPicking old_data: %s", old_data)
        res = super().write(vals)

        if not old_data:
            return res

        for picking in self:
            if picking.carrier_id.delivery_type != 'redbox':
                continue

            new_data = picking._get_redbox_items_json()
            old_items = old_data.get(picking.id)

            if old_items != new_data:
                _logger.warning("🚚 Redbox items changed for %s", picking.name)
                picking._send_update_to_redbox(json.loads(new_data))
            else:
                _logger.warning("🚚 No change in items for %s", picking.name)

        return res
    def _get_redbox_items(self):
        self.ensure_one()

        moves = getattr(self, 'move_ids_without_package', False) or self.move_ids

        items = []

        for move in moves:
            if move.state == 'cancel' or move.product_id.type == 'service':
                continue

            qty = getattr(move, 'quantity', move.product_uom_qty)

            items.append({
                "name": move.product_id.name,
                "quantity": qty,
                "unit_price": move.sale_line_id.price_unit if move.sale_line_id else 0,
            })

        return items

    def _get_redbox_items_json(self):
        """Return items as JSON string for comparison"""
        items = self._get_redbox_items()
        return json.dumps(items, sort_keys=True)

    def _send_update_to_redbox(self, items):
        """
        Send shipment update to Redbox API.
        """
        self.ensure_one()
        _logger.info("REDBOX _send_update_to_redbox called: %s", items)
        payload = {
            "items": items
        }
        try:
            url = "https://api.redboxsa.com/v3/shipments/%s" % self.carrier_tracking_ref
            headers = {
                "Authorization": f"Bearer {self.carrier_id.redbox_api_key}"
            }
            _logger.info("=== REDBOX REQUEST ===")
            _logger.info("URL: %s", url)
            _logger.info("Method: PUT")
            _logger.info("Headers: %s", headers)
            _logger.info("Payload: %s", payload)
            response = requests.put(
                url,
                json=payload,
                headers=headers,
                timeout=50
            )
            _logger.info("=== REDBOX RESPONSE ===")
            _logger.info("Status: %s", response.status_code)
            _logger.info("Response: %s", response.text)
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            _logger.error("Redbox API Connection Error for order %s: %s", self.name, e)
            return

        if data.get('success'):
            _logger.info("REDBOX shipment updated successfully for order %s. Tracking: %s", self.name, self.carrier_tracking_ref)
        else:
            self.message_post(body=_('Redbox API Error: %s') % data.get('msg'))
            _logger.error("Redbox Update  Shipment API Error for order %s: %s", self.name, data.get('msg'))
    def action_cancel(self):
        res = super().action_cancel()
        for picking in self:
            if picking.carrier_id and picking.carrier_id.delivery_type == 'redbox':
                try:
                    picking._cancel_redbox_shipment()
                except Exception as e:
                    _logger.exception("❌ Redbox cancel failed for %s", picking.name)
                    picking.message_post(
                        body=_("Redbox cancel failed: %s") % str(e)
                    )

        return res
    def _cancel_redbox_shipment(self):
        """
        Cancel the shipment in Redbox.
        """
        self.ensure_one()
        _logger.info("REDBOX _cancel_redbox_shipment called: %s", self.name)
        payload = {}
        try:
            url = "https://api.redboxsa.com/v3/shipments/%s/cancel" % self.carrier_tracking_ref
            headers = {
                "Authorization": f"Bearer {self.carrier_id.redbox_api_key}"
            }
            _logger.info("=== REDBOX REQUEST ===")
            _logger.info("URL: %s", url)
            _logger.info("Method: POST")
            _logger.info("Headers: %s", headers)
            _logger.info("Payload: %s", payload)
            response = requests.post(
                url,
                json=payload,
                headers=headers,
                timeout=50
            )
            _logger.info("=== REDBOX RESPONSE ===")
            _logger.info("Status: %s", response.status_code)
            _logger.info("Response: %s", response.text)
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            _logger.error("Redbox API Connection Error for order %s: %s", self.name, e)
            return

        if data.get('success'):
            _logger.info("REDBOX shipment cancelled successfully for order %s. Tracking: %s", self.name, self.carrier_tracking_ref)
        else:
            _logger.error("Redbox Update  Shipment API Error for order %s: %s", self.name, data.get('msg'))
    def _handle_redbox_status(self, status, data):
        """
        Handle Redbox shipment status update and auto-validate picking if delivered.
        """
        self.ensure_one()

        _logger.warning("[Redbox] Update status '%s' for picking '%s'", status, self.name)
        self.sudo().write({'redbox_shipment_status': status})

        status_clean = (status or '').strip().lower()

        if status_clean == 'delivered' and self.state != 'done':
            _logger.warning("[Redbox] Auto-validating picking '%s'", self.name)

            # Reserve
            self.sudo().action_assign()

            # Compatible moves (Odoo 17 & 19)
            moves = getattr(self, 'move_ids_without_package', False) or self.move_ids

            for move in moves:
                demand_qty = move.product_uom_qty

                # Ensure move lines exist
                if not move.move_line_ids:
                    vals = {
                        'move_id': move.id,
                        'picking_id': self.id,
                        'product_id': move.product_id.id,
                        'product_uom_id': move.product_uom.id,
                        'location_id': move.location_id.id,
                        'location_dest_id': move.location_dest_id.id,
                    }

                    # Field difference between Odoo versions
                    if 'quantity' in self.env['stock.move.line']._fields:
                        vals['quantity'] = demand_qty
                    else:
                        vals['qty_done'] = demand_qty

                    self.env['stock.move.line'].sudo().create(vals)

                # Set done qty
                for line in move.move_line_ids:
                    if 'quantity' in line._fields:
                        if not line.quantity:
                            line.quantity = demand_qty
                    elif 'qty_done' in line._fields:
                        if not line.qty_done:
                            line.qty_done = demand_qty

            # Validate
            res = self.sudo().button_validate()

            # Handle wizard (multi-version safe)
            if isinstance(res, dict):
                model = res.get('res_model')
                res_id = res.get('res_id')

                if model and res_id:
                    wizard = self.env[model].browse(res_id)
                    _logger.warning("[Redbox] Processing wizard '%s'", model)
                    if hasattr(wizard, 'process'):
                        wizard.process()
                    elif hasattr(wizard, 'action_confirm'):
                        wizard.action_confirm()

            _logger.warning("[Redbox] Picking '%s' state after validate: %s", self.name, self.state)

        elif status_clean in ['in-transit']:
            _logger.warning("[Redbox] Picking '%s' is in transit", self.name)

        elif status_clean in ['not-delivered', 'cancelled']:
            _logger.warning("[Redbox] Picking '%s' delivery failed or cancelled", self.name)
    
    def _update_redbox_shipment(self):
        self.ensure_one()

        order = self.sale_id

        items = []
        for line in order.order_line:
            if line.is_delivery:
                continue

            items.append({
                "name": line.product_id.name,
                "quantity": line.product_uom_qty,
                "unit_price": line.price_unit,
            })
        
        payload = {
            "items": items,
            "cod_amount": (order.amount_total - order.amount_paid),
            "cod_currency": order.currency_id.name,
        }
            
        _logger.warning("📦 Redbox payload update: %s", payload)

        _logger.info("REDBOX _send_update_to_redbox called: %s", items)
        
        try:
            url = "https://api.redboxsa.com/v3/shipments/%s" % self.carrier_tracking_ref
            headers = {
                "Authorization": f"Bearer {self.carrier_id.redbox_api_key}"
            }
            _logger.info("=== REDBOX REQUEST ===")
            _logger.info("URL: %s", url)
            _logger.info("Method: PUT")
            _logger.info("Headers: %s", headers)
            _logger.info("Payload: %s", payload)
            response = requests.put(
                url,
                json=payload,
                headers=headers,
                timeout=50
            )
            _logger.info("=== REDBOX RESPONSE ===")
            _logger.info("Status: %s", response.status_code)
            _logger.info("Response: %s", response.text)
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            _logger.error("Redbox API Connection Error for order %s: %s", self.name, e)
            return

        if data.get('success'):
            _logger.info("REDBOX shipment updated successfully for order %s. Tracking: %s", self.name, self.carrier_tracking_ref)
        else:
            self.message_post(body=_('Redbox API Error: %s') % data.get('msg'))
            _logger.error("Redbox Update  Shipment API Error for order %s: %s", self.name, data.get('msg'))
            