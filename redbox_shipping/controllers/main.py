import hmac
import json
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


def _text_response(body, status=200):
    return request.make_response(body, headers=[('Content-Type', 'text/plain')], status=status)


class RedboxWebhookController(http.Controller):

    @http.route('/redbox/webhook', type='http', auth='public', methods=['POST'], csrf=False)
    def redbox_webhook(self, **kwargs):
        webhook_token = request.httprequest.headers.get('redbox-webhook-token', '')
        if not webhook_token:
            _logger.warning("RedBox webhook: missing token")
            return _text_response('Unauthorized', 401)

        try:
            data = json.loads(request.httprequest.get_data().decode('utf-8'))
        except (UnicodeDecodeError, ValueError):
            _logger.warning("RedBox webhook: invalid JSON")
            return _text_response('Invalid JSON', 400)
        if not isinstance(data, dict):
            return _text_response('Invalid payload', 400)

        # Resolve the carrier from the token (constant-time comparison).
        carrier = request.env['delivery.carrier'].browse()
        for candidate in request.env['delivery.carrier'].sudo().search([('delivery_type', '=', 'redbox')]):
            if candidate.redbox_webhook_token and hmac.compare_digest(
                    candidate.redbox_webhook_token, webhook_token):
                carrier = candidate
                break
        if not carrier:
            _logger.warning("RedBox webhook: invalid token")
            return _text_response('Unauthorized', 401)

        shipment_id = data.get('shipment_id')
        tracking_number = data.get('tracking_number')
        if not data.get('status_name') or not (shipment_id or tracking_number):
            return _text_response('Missing shipment_id/tracking_number or status_name', 400)

        _logger.info("RedBox webhook: carrier=%s tracking=%s status=%s code=%s",
                     carrier.id, tracking_number, data.get('status_name'), data.get('status_code'))

        try:
            Picking = request.env['stock.picking'].sudo()
            picking = Picking.browse()
            if shipment_id:
                picking = Picking.search([
                    ('carrier_id', '=', carrier.id),
                    ('redbox_shipment_id', '=', str(shipment_id)),
                ], limit=1)
            if not picking and tracking_number:
                picking = Picking.search([
                    ('carrier_id', '=', carrier.id),
                    ('carrier_tracking_ref', '=', str(tracking_number)),
                ], limit=1)
            if not picking:
                # Answer 2xx so RedBox does not retry events that belong to another
                # carrier/database sharing the same API key.
                _logger.info("RedBox webhook: no picking for tracking=%s carrier=%s",
                             tracking_number, carrier.id)
                return _text_response('Ignored', 200)

            result = picking._redbox_apply_status(data)
            return _text_response(result.upper() if result else 'OK', 200)
        except Exception:
            _logger.exception("RedBox webhook: error processing tracking=%s", tracking_number)
            return _text_response('Internal Server Error', 500)
