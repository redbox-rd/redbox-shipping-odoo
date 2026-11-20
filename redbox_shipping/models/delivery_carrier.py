import logging
import secrets
from urllib.parse import quote

import requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

REDBOX_PROD_URL = 'https://api.redboxsa.com'
REDBOX_STAGE_URL = 'https://stage.api.redboxsa.com'
REDBOX_TRACKING_URL = 'https://redboxsa.com/ar/collect/parcel-tracking?tracking_number=%s'
REDBOX_MAX_COD = 10000


class DeliveryCarrier(models.Model):
    _inherit = 'delivery.carrier'

    delivery_type = fields.Selection(
        selection_add=[('redbox', 'RedBox')],
        ondelete={'redbox': 'set default'},
    )
    redbox_api_key = fields.Char(
        string="RedBox API Key",
        groups="base.group_system",
        help="Bearer token provided by RedBox. Use the sandbox token while the carrier "
             "is in Test Environment and the production token in Production.",
    )
    redbox_webhook_created = fields.Boolean(
        string="RedBox Webhook Registered",
        default=False,
        copy=False,
        help="Indicates if the RedBox status webhook has been registered for this carrier.",
    )
    redbox_webhook_token = fields.Char(
        string="Webhook Token",
        groups="base.group_system",
        copy=False,
        default=lambda self: secrets.token_urlsafe(32),
    )
    redbox_fixed_price = fields.Float(
        string="RedBox Fixed Price",
        default=12.0,
        help="Fixed price charged to the customer for RedBox shipping.",
    )
    redbox_home_delivery = fields.Boolean(
        string="RedBox Home Delivery",
        default=False,
        help="When enabled, shipments are created as RedBox Home Delivery "
             "instead of point (locker) pickup.",
    )
    redbox_allow_cod = fields.Boolean(
        string="Allow Cash on Delivery",
        default=False,
        help="When disabled, orders shipped with RedBox must be fully paid: Cash on Delivery "
             "payment methods are hidden and rejected at checkout, and unpaid orders cannot "
             "be shipped. When enabled, the unpaid balance is collected by RedBox as COD.",
    )
    blocked_payment_provider_ids = fields.Many2many(
        'payment.provider',
        'delivery_carrier_payment_provider_rel',
        'carrier_id', 'provider_id',
        string="Blocked Payment Methods",
        help="Payment providers that cannot be used when the customer chooses this carrier. "
             "On Odoo 19+ the built-in Cash on Delivery provider is blocked automatically "
             "when 'Allow Cash on Delivery' is off; on Odoo 17/18 select your COD provider here.",
    )
    redbox_auto_validate_return = fields.Boolean(
        string="Auto-validate Returns",
        default=True,
        help="Automatically validate the Odoo return picking when RedBox reports the "
             "return parcel as 'Returned' to the business.",
    )

    # -------------------------------------------------------------------------
    # API client
    # -------------------------------------------------------------------------

    def _redbox_base_url(self):
        self.ensure_one()
        return REDBOX_PROD_URL if self.prod_environment else REDBOX_STAGE_URL

    def _redbox_call(self, method, path, payload=None, timeout=30):
        """Low-level call. Returns (http_status, json_dict). Never raises on HTTP errors."""
        self.ensure_one()
        api_key = self.sudo().redbox_api_key
        if not api_key:
            raise UserError(_("RedBox API key is not configured for carrier %s.", self.name))
        url = self._redbox_base_url() + path
        try:
            response = requests.request(
                method, url,
                json=payload if payload is not None else None,
                headers={
                    'Authorization': 'Bearer %s' % api_key,
                    'Accept': 'application/json',
                },
                timeout=timeout,
            )
        except requests.exceptions.RequestException as e:
            _logger.warning("RedBox %s %s connection error: %s", method, path, e)
            raise UserError(_("Could not connect to RedBox: %s", e)) from e
        try:
            data = response.json()
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {'data': data}
        _logger.debug("RedBox %s %s -> %s %s", method, path, response.status_code, data)
        return response.status_code, data

    def _redbox_request(self, method, path, payload=None, timeout=30):
        """Call RedBox and raise a UserError with the API message on failure."""
        status, data = self._redbox_call(method, path, payload=payload, timeout=timeout)
        if status >= 400 or not data.get('success'):
            msg = data.get('msg') or data.get('message') or _("HTTP %s", status)
            _logger.warning("RedBox %s %s failed: %s %s", method, path, status, msg)
            raise UserError(_("RedBox API error: %s", msg))
        return data

    # -------------------------------------------------------------------------
    # Odoo delivery framework hooks
    # -------------------------------------------------------------------------

    def redbox_rate_shipment(self, order):
        return {
            'success': True,
            'price': self.redbox_fixed_price,
            'error_message': False,
            'warning_message': False,
        }

    def redbox_send_shipping(self, pickings):
        """Create the RedBox shipment. Called by Odoo when the delivery is validated
        (or 'Send to Shipper' is pressed)."""
        result = []
        for picking in pickings:
            carrier = picking.carrier_id or self
            if picking.redbox_shipment_id:
                # Already shipped: never create a second shipment.
                result.append({
                    'exact_price': carrier.redbox_fixed_price,
                    'tracking_number': picking.carrier_tracking_ref,
                })
                continue
            payload = picking._redbox_prepare_shipment_payload()
            data = carrier._redbox_create_shipment(picking, payload)
            tracking = data.get('tracking_number')
            picking.write({
                'redbox_shipment_id': data.get('shipment_id'),
                'carrier_tracking_ref': tracking,
                'redbox_label_url': data.get('shipping_label_url'),
                'redbox_shipment_status': 'Pending',  # initial RedBox status (code 101)
                'redbox_status_code': 0,
                'redbox_status_date': False,
                'redbox_cod_amount': payload.get('cod_amount') or 0.0,
                'redbox_reference': payload.get('reference'),
                'redbox_send_count': picking.redbox_send_count + 1,
            })
            picking._redbox_attach_label(data.get('shipping_label_url'), tracking)
            result.append({
                'exact_price': carrier.redbox_fixed_price,
                'tracking_number': tracking,
            })
        return result

    def _redbox_create_shipment(self, picking, payload):
        """POST /v3/shipments, recovering from 409 'Shipment is existed'.

        RedBox rejects a reference it already knows. That happens when:
        - the shipment was created but the Odoo transaction rolled back afterwards
          (the shipment is then very recent, still Pending, same customer): adopt it;
        - another database / an older or cancelled shipment used the same reference:
          retry once with a reference unique to this picking.
        Note: `payload` is updated in place with the reference actually used.
        """
        self.ensure_one()
        status, data = self._redbox_call('POST', '/v3/shipments', payload, timeout=60)
        if status == 409:
            existing = self._redbox_find_recent_shipment(payload)
            if existing:
                _logger.info("RedBox: adopting existing shipment %s for reference %s",
                             existing.get('id'), payload['reference'])
                label = self._redbox_call(
                    'GET', '/v3/shipments/%s/label' % quote(existing['id'], safe=''))[1]
                return {
                    'success': True,
                    'shipment_id': existing.get('id'),
                    'tracking_number': existing.get('tracking_number'),
                    'shipping_label_url': label.get('url'),
                }
            payload['reference'] = '%s-P%s' % (payload['reference'], picking.id)
            _logger.info("RedBox: reference already used, retrying with %s", payload['reference'])
            status, data = self._redbox_call('POST', '/v3/shipments', payload, timeout=60)
        if status >= 400 or not data.get('success'):
            msg = data.get('msg') or _("HTTP %s", status)
            raise UserError(_("RedBox API error: %s", msg))
        return data

    def _redbox_find_recent_shipment(self, payload, pages=4, page_size=50):
        """Look for a just-created shipment matching the payload in the latest shipments
        (the API cannot filter by reference)."""
        for page in range(1, pages + 1):
            status, data = self._redbox_call(
                'GET', '/v3/shipments?page=%s&page_size=%s' % (page, page_size))
            shipments = data.get('shipments') or []
            for shipment in shipments:
                if (shipment.get('reference') == payload['reference']
                        and not shipment.get('is_return')
                        and (shipment.get('status_name') or '').lower() == 'pending'
                        and (shipment.get('customer') or {}).get('phone') == payload.get('customer_phone')
                        and abs(float((shipment.get('cod') or {}).get('amount') or 0.0)
                                - float(payload.get('cod_amount') or 0.0)) < 0.01):
                    return shipment
            if status >= 400 or len(shipments) < page_size:
                break
        return None

    def redbox_get_tracking_link(self, picking):
        if picking.carrier_tracking_ref:
            return REDBOX_TRACKING_URL % quote(picking.carrier_tracking_ref)
        return False

    def redbox_cancel_shipment(self, pickings):
        for picking in pickings:
            identifier = picking.redbox_shipment_id or picking.carrier_tracking_ref
            if not identifier:
                continue
            carrier = picking.carrier_id or self
            carrier._redbox_request('POST', '/v3/shipments/%s/cancel' % quote(identifier, safe=''))
            picking.write({
                'redbox_shipment_id': False,
                'redbox_label_url': False,
                'redbox_shipment_status': 'Canceled',
                'redbox_status_date': False,
                'redbox_cod_amount': 0.0,
            })

    def _compute_can_generate_return(self):
        super()._compute_can_generate_return()
        for carrier in self:
            if carrier.delivery_type == 'redbox':
                carrier.can_generate_return = True

    def redbox_get_return_label(self, pickings, tracking_number=None, origin_date=None):
        for picking in pickings:
            if picking.redbox_can_create_return:
                picking._redbox_create_return()
            elif picking.picking_type_code == 'outgoing':
                # e.g. "Generate Return Label" on delivery: RedBox returns can only be
                # created once the original shipment has been delivered.
                picking.message_post(body=_(
                    "RedBox return labels are created from the return picking, once the "
                    "original shipment has been delivered."))

    # -------------------------------------------------------------------------
    # Payment / COD policy
    # -------------------------------------------------------------------------

    def _redbox_get_blocked_providers(self):
        """Payment providers that must not be used with this carrier."""
        self.ensure_one()
        blocked = self.sudo().blocked_payment_provider_ids
        if self.delivery_type == 'redbox' and not self.redbox_allow_cod:
            Provider = self.env['payment.provider'].sudo()
            if 'custom_mode' in Provider._fields:
                blocked |= Provider.search([('custom_mode', '=', 'cash_on_delivery')])
        return blocked

    @api.model
    def _redbox_sync_cod_vals(self, vals):
        # Odoo 19+ has a native 'allow_cash_on_delivery' flag on carriers; keep it in sync.
        if 'redbox_allow_cod' in vals and 'allow_cash_on_delivery' in self._fields:
            vals['allow_cash_on_delivery'] = vals['redbox_allow_cod']
        return vals

    # -------------------------------------------------------------------------
    # Webhook registration
    # -------------------------------------------------------------------------

    def _redbox_webhook_original_id(self):
        self.ensure_one()
        return 'odoo-%s-%s' % (self.env.cr.dbname, self.id)

    def _redbox_register_webhook(self, raise_on_error=False):
        self.ensure_one()
        try:
            base_url = self.get_base_url()
            if not base_url.startswith('https://'):
                _logger.warning(
                    "RedBox webhook URL %s is not HTTPS; RedBox may not be able to reach it.",
                    base_url)
            original_id = self._redbox_webhook_original_id()
            # Remove previous registrations (this carrier, and the legacy per-db id) so
            # a new token / URL always takes effect.
            for old_id in (original_id, 'odoo-%s' % self.env.cr.dbname):
                self._redbox_call('DELETE', '/v3/webhooks/%s' % quote(old_id, safe=''))
            payload = {
                'event': 'shipment.status.update',
                'original_id': original_id,
                'subscriber': self.env.cr.dbname,
                'target_url': '%s/redbox/webhook' % base_url.rstrip('/'),
                'headers': {'redbox-webhook-token': self.sudo().redbox_webhook_token or ''},
            }
            status, data = self._redbox_call('POST', '/v3/webhooks', payload, timeout=15)
            if status == 409:
                _logger.warning("RedBox webhook %s already exists.", original_id)
            elif status >= 400 or not data.get('success'):
                raise UserError(_("RedBox webhook registration failed: %s",
                                  data.get('msg') or _("HTTP %s", status)))
            super(DeliveryCarrier, self).write({'redbox_webhook_created': True})
            _logger.info("RedBox webhook registered for carrier %s (%s)", self.name, original_id)
            return True
        except Exception as e:
            if raise_on_error:
                raise
            _logger.error("RedBox webhook registration failed for carrier %s: %s", self.name, e)
            return False

    def action_redbox_register_webhook(self):
        for carrier in self:
            carrier._redbox_register_webhook(raise_on_error=True)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("RedBox"),
                'message': _("Webhook registered successfully."),
                'type': 'success',
                'sticky': False,
            },
        }

    # -------------------------------------------------------------------------
    # ORM
    # -------------------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        vals_list = [self._redbox_sync_cod_vals(dict(vals)) for vals in vals_list]
        records = super().create(vals_list)
        for rec in records:
            if rec.delivery_type == 'redbox' and rec.sudo().redbox_api_key:
                rec._redbox_register_webhook()
        return records

    def write(self, vals):
        vals = self._redbox_sync_cod_vals(dict(vals))
        reconnect = bool({'redbox_api_key', 'prod_environment'} & set(vals))
        if reconnect:
            vals.setdefault('redbox_webhook_created', False)
        res = super().write(vals)
        if reconnect:
            for rec in self:
                if rec.delivery_type == 'redbox' and rec.sudo().redbox_api_key:
                    rec._redbox_register_webhook()
        return res

    @api.onchange('delivery_type')
    def _onchange_delivery_type_redbox(self):
        if self.delivery_type == 'redbox':
            self.name = "RedBox Home Delivery" if self.redbox_home_delivery else "RedBox"
            sa = self.env.ref('base.sa', raise_if_not_found=False)
            if sa:
                self.country_ids = [(6, 0, [sa.id])]
                states = self.env['res.country.state'].search([('country_id', '=', sa.id)])
                self.state_ids = [(6, 0, states.ids)]

    @api.onchange('redbox_home_delivery')
    def _onchange_redbox_home_delivery(self):
        if self.delivery_type == 'redbox':
            self.name = "RedBox Home Delivery" if self.redbox_home_delivery else "RedBox"

    @api.onchange('redbox_allow_cod')
    def _onchange_redbox_allow_cod(self):
        if 'allow_cash_on_delivery' in self._fields:
            self.allow_cash_on_delivery = self.redbox_allow_cod
