import logging
from urllib.parse import quote

import requests
from dateutil import parser as date_parser
from dateutil import tz

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .delivery_carrier import REDBOX_MAX_COD

_logger = logging.getLogger(__name__)

# RedBox status names (see API docs "Shipment statuses")
REDBOX_STATUS_RETURNED = 'returned'
REDBOX_ALERT_STATUSES = ('canceled', 'cancelled', 'not-delivered', 'not-deposited')


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    redbox_delivery_type = fields.Selection(related='carrier_id.delivery_type', string="Carrier Type")
    redbox_shipment_id = fields.Char(string="RedBox Shipment ID", copy=False, readonly=True)
    redbox_reference = fields.Char(string="RedBox Reference", copy=False, readonly=True)
    redbox_label_url = fields.Char(string="Shipping Label URL", copy=False)
    redbox_shipment_status = fields.Char(string="RedBox Shipment Status", copy=False)
    redbox_status_code = fields.Integer(string="RedBox Status Code", copy=False)
    redbox_status_date = fields.Datetime(string="RedBox Status Date", copy=False)
    redbox_return_code = fields.Char(
        string="RedBox Return Code", copy=False, readonly=True,
        help="Code the customer uses to drop the return parcel at a RedBox locker.")
    redbox_cod_amount = fields.Float(string="RedBox COD Amount", copy=False, readonly=True)
    redbox_send_count = fields.Integer(copy=False, readonly=True)
    redbox_origin_picking_id = fields.Many2one(
        'stock.picking', string="RedBox Original Delivery",
        compute='_compute_redbox_origin_picking')
    redbox_can_create_return = fields.Boolean(compute='_compute_redbox_origin_picking')

    @api.depends('move_ids.origin_returned_move_id', 'picking_type_code', 'state', 'redbox_shipment_id')
    def _compute_redbox_origin_picking(self):
        for picking in self:
            origin = picking.move_ids.origin_returned_move_id.picking_id.filtered(
                lambda p: p.carrier_id.delivery_type == 'redbox')[:1]
            picking.redbox_origin_picking_id = origin
            picking.redbox_can_create_return = bool(
                origin
                and picking.picking_type_code == 'incoming'
                and not picking.redbox_shipment_id
                and picking.state not in ('done', 'cancel')
            )

    # -------------------------------------------------------------------------
    # Actions
    # -------------------------------------------------------------------------

    def action_open_redbox_label(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_url',
            'url': self.redbox_label_url,
            'target': 'new',
        }

    def action_redbox_create_return(self):
        for picking in self:
            picking._redbox_create_return()
        return True

    # -------------------------------------------------------------------------
    # Shipment payload
    # -------------------------------------------------------------------------

    def _redbox_get_items(self, use_demand=False):
        self.ensure_one()
        items = []
        for move in self.move_ids:
            if move.state == 'cancel' or move.product_id.type == 'service':
                continue
            qty = move.product_uom_qty if use_demand else (move.quantity or move.product_uom_qty)
            if not qty:
                continue
            line = move.sale_line_id
            if line:
                currency = line.order_id.currency_id
                unit_price = line.price_reduce_taxinc if 'price_reduce_taxinc' in line._fields else line.price_unit
            else:
                currency = self.company_id.currency_id
                unit_price = move.product_id.lst_price
            item = {
                'name': move.product_id.name,  # SKU is sent separately
                'quantity': qty,
                'unit_price': currency.round(unit_price),
                'currency': currency.name,
            }
            if move.product_id.default_code:
                item['sku'] = move.product_id.default_code
            items.append(item)
        return items

    def _redbox_get_reference(self):
        self.ensure_one()
        order = self.sale_id
        base = order.name if order else self.name
        other_shipped = order and order.picking_ids.filtered(
            lambda p: p != self and p.redbox_reference and p.picking_type_code == 'outgoing')
        if self.backorder_id or other_shipped:
            base = '%s-%s' % (base, self.name)
        # RedBox treats a known reference as the same shipment (idempotent), so a
        # re-send after cancellation needs a new reference.
        if self.redbox_send_count:
            base = '%s-%s' % (base, self.redbox_send_count + 1)
        return base

    def _redbox_get_order_paid_amount(self, order):
        """Amount already paid on the order (online transactions or invoice payments)."""
        tx_paid = sum(
            tx.amount for tx in order.sudo().transaction_ids
            if tx.state in ('done', 'authorized')
        )
        invoices = order.sudo().invoice_ids.filtered(
            lambda m: m.state == 'posted' and m.move_type == 'out_invoice')
        inv_paid = sum(inv.amount_total - inv.amount_residual for inv in invoices)
        return max(tx_paid, inv_paid)

    def _redbox_get_cod_amount(self):
        self.ensure_one()
        order = self.sale_id
        if not order:
            return 0.0
        currency = order.currency_id
        unpaid = order.amount_total - self._redbox_get_order_paid_amount(order)
        # COD already assigned to other active RedBox shipments of the same order
        other_cod = sum(order.picking_ids.filtered(
            lambda p: p != self and p.redbox_shipment_id).mapped('redbox_cod_amount'))
        unpaid = currency.round(max(unpaid - other_cod, 0.0))
        if currency.is_zero(unpaid):
            return 0.0
        if not self.carrier_id.redbox_allow_cod:
            raise UserError(_(
                "The carrier %(carrier)s does not allow Cash on Delivery, but order %(order)s "
                "has an unpaid balance of %(amount)s %(currency)s. Register the payment first "
                "or enable 'Allow Cash on Delivery' on the carrier.",
                carrier=self.carrier_id.name, order=order.name,
                amount=unpaid, currency=currency.name))
        if unpaid > REDBOX_MAX_COD:
            raise UserError(_(
                "RedBox does not accept a COD amount above %(max)s (order %(order)s: %(amount)s).",
                max=REDBOX_MAX_COD, order=order.name, amount=unpaid))
        return unpaid

    def _redbox_prepare_shipment_payload(self):
        self.ensure_one()
        carrier = self.carrier_id
        order = self.sale_id
        partner = self.partner_id or (order and order.partner_shipping_id)
        if not partner:
            raise UserError(_("Picking %s has no delivery address.", self.name))
        phone = partner.phone or getattr(partner, 'mobile', False)
        if not phone:
            raise UserError(_("RedBox requires a phone number for %s.", partner.display_name))
        city = partner.city or partner.state_id.name or ''
        if carrier.redbox_home_delivery and not city:
            raise UserError(_("RedBox Home Delivery requires a city for %s.", partner.display_name))
        currency = order.currency_id if order else self.company_id.currency_id

        payload = {
            'reference': self._redbox_get_reference(),
            'customer_name': partner.name or partner.display_name,
            'customer_phone': phone,
            'customer_email': partner.email or '',
            'customer_address': ', '.join(filter(None, [partner.street, partner.street2])) or city,
            'customer_city': city,
            'customer_country': partner.country_id.name or '',
            'cod_amount': self._redbox_get_cod_amount(),
            'cod_currency': currency.name,
            'items': self._redbox_get_items(),
        }
        if carrier.redbox_home_delivery:
            payload['delivery_method'] = 'home_delivery'
        weight = self.shipping_weight or self.weight
        if weight:
            payload['weight_value'] = weight
            payload['weight_unit'] = self.env['product.template']._get_weight_uom_name_from_ir_config_parameter()
        if order:
            delivery_lines = order.order_line.filtered('is_delivery')
            if delivery_lines:
                payload['shipping_price'] = currency.round(sum(delivery_lines.mapped('price_total')))
                payload['shipping_price_currency'] = currency.name
        return payload

    def _redbox_attach_label(self, url, tracking):
        if not url:
            return
        try:
            response = requests.get(url, timeout=30)
            response.raise_for_status()
        except Exception as e:  # label download must never block the shipment
            _logger.warning("RedBox label download failed for %s: %s", self.name, e)
            return
        self.message_post(
            body=_("RedBox shipping label (tracking %s).", tracking),
            attachments=[('RedBox-%s.pdf' % (tracking or self.name), response.content)],
        )

    # -------------------------------------------------------------------------
    # Returns
    # -------------------------------------------------------------------------

    def _redbox_ensure_shipment_id(self):
        """Return the RedBox shipment_id, fetching it for shipments created by older
        versions of the module that only stored the tracking number."""
        self.ensure_one()
        if self.redbox_shipment_id:
            return self.redbox_shipment_id
        if not self.carrier_tracking_ref:
            raise UserError(_("Delivery %s has no RedBox shipment.", self.name))
        data = self.carrier_id._redbox_request(
            'GET', '/v3/shipments/%s' % quote(self.carrier_tracking_ref, safe=''))
        shipment_id = (data.get('shipment') or {}).get('id')
        if not shipment_id:
            raise UserError(_("RedBox shipment %s not found.", self.carrier_tracking_ref))
        self.write({'redbox_shipment_id': shipment_id})
        return shipment_id

    def _redbox_create_return(self):
        self.ensure_one()
        if self.redbox_shipment_id:
            raise UserError(_("A RedBox return already exists for %s.", self.name))
        origin = self.redbox_origin_picking_id
        if not origin:
            raise UserError(_("%s is not a return of a RedBox delivery.", self.name))
        carrier = origin.carrier_id
        payload = {
            'original_shipment_id': origin._redbox_ensure_shipment_id(),
            'reference': self.name,
        }
        items = self._redbox_get_items(use_demand=True)
        if items:
            payload['items'] = items
        data = carrier._redbox_request('POST', '/v3/shipments/returns', payload, timeout=60)
        vals = {
            'redbox_shipment_id': data.get('shipment_id'),
            'carrier_tracking_ref': data.get('tracking_number'),
            'redbox_label_url': data.get('shipping_label_url'),
            'redbox_return_code': data.get('return_code'),
            'redbox_reference': self.name,
            'redbox_shipment_status': 'R-Waiting',
            'redbox_status_code': 0,
            'redbox_status_date': False,
        }
        if not self.carrier_id:
            vals['carrier_id'] = carrier.id
        self.write(vals)
        body = _("RedBox return created. Tracking: %s", data.get('tracking_number'))
        if data.get('return_code'):
            body = _("%(body)s. Return code for the customer: %(code)s",
                     body=body, code=data.get('return_code'))
        self.message_post(body=body)
        self._redbox_attach_label(data.get('shipping_label_url'), data.get('tracking_number'))
        return data

    def _redbox_validate_return(self):
        self.ensure_one()
        if self.state == 'draft':
            self.action_confirm()
        if self.state in ('confirmed', 'waiting'):
            self.action_assign()
        for move in self.move_ids.filtered(lambda m: m.state not in ('done', 'cancel')):
            if move.quantity != move.product_uom_qty:
                move.quantity = move.product_uom_qty
            if 'picked' in move._fields:
                move.picked = True
        res = self.with_context(
            skip_backorder=True, cancel_backorder=True,
            skip_sms=True, skip_immediate=True,
            picking_ids_not_to_backorder=self.ids,
        ).button_validate()
        if self.state != 'done':
            raise UserError(_("Manual validation required (%s).",
                              isinstance(res, dict) and res.get('res_model') or res))

    # -------------------------------------------------------------------------
    # Webhook status
    # -------------------------------------------------------------------------

    @api.model
    def _redbox_parse_date(self, value):
        if not value:
            return False
        try:
            dt = date_parser.isoparse(value)
        except (ValueError, TypeError):
            return False
        if dt.tzinfo:
            dt = dt.astimezone(tz.UTC).replace(tzinfo=None)
        return dt.replace(microsecond=0)

    def _redbox_apply_status(self, data):
        """Apply a RedBox `shipment.status.update` webhook payload. Returns a short result."""
        self.ensure_one()
        status = (data.get('status_name') or '').strip()
        try:
            code = int(data.get('status_code') or 0)
        except (TypeError, ValueError):
            code = 0
        event_date = self._redbox_parse_date(data.get('date'))

        if event_date and self.redbox_status_date:
            if event_date < self.redbox_status_date:
                return 'stale'
            if event_date == self.redbox_status_date and code == self.redbox_status_code \
                    and status == self.redbox_shipment_status:
                return 'duplicate'

        changed = status != self.redbox_shipment_status or code != self.redbox_status_code
        vals = {
            'redbox_shipment_status': status,
            'redbox_status_code': code,
            'redbox_status_date': event_date or fields.Datetime.now(),
        }
        if not self.redbox_shipment_id and data.get('shipment_id'):
            vals['redbox_shipment_id'] = data['shipment_id']
        self.write(vals)
        if not changed:
            return 'unchanged'

        label = data.get('status_label') or ''
        self.message_post(body=_("RedBox status: %(status)s%(label)s",
                                 status=status, label=(' - %s' % label) if label else ''))

        status_l = status.lower()
        if status_l == REDBOX_STATUS_RETURNED and self.picking_type_code == 'incoming' \
                and self.state not in ('done', 'cancel') and self.carrier_id.redbox_auto_validate_return:
            try:
                with self.env.cr.savepoint():
                    self._redbox_validate_return()
                self.message_post(body=_("Return validated automatically after RedBox 'Returned' status."))
            except Exception as e:
                _logger.warning("RedBox auto-validation failed for %s: %s", self.name, e)
                self.message_post(body=_("Automatic validation of the return failed: %s", e))
                self._redbox_schedule_alert(status)
        elif status_l in REDBOX_ALERT_STATUSES:
            self._redbox_schedule_alert(status)
        return 'ok'

    def _redbox_schedule_alert(self, status):
        self.ensure_one()
        user = self.user_id or self.sale_id.user_id or self.env.ref('base.user_admin', raise_if_not_found=False)
        act_type = self.env.ref('mail.mail_activity_data_warning', raise_if_not_found=False) \
            or self.env.ref('mail.mail_activity_data_todo', raise_if_not_found=False)
        if not act_type or not user:
            return
        self.activity_schedule(
            activity_type_id=act_type.id,
            user_id=user.id,
            summary=_("RedBox shipment %s", status),
            note=_("RedBox reported status '%(status)s' for tracking %(tracking)s. "
                   "Please follow up (e.g. create a return to restock the goods).",
                   status=status, tracking=self.carrier_tracking_ref or ''),
        )
