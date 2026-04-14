import json
import logging
from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)

class RedboxWebhookController(http.Controller):

    @http.route('/redbox/webhook', type='http', auth='public', methods=['POST'], csrf=False)
    def redbox_webhook(self, **kwargs):
        try:
            raw_data = request.httprequest.data
            data = json.loads(raw_data.decode('utf-8'))

            _logger.info("Redbox webhook received: %s", data)

            tracking_number = data.get('tracking_number')
            status_name = data.get('status_name')

            if not tracking_number:
                return request.make_response(
                    'Missing tracking_number',
                    headers=[('Content-Type', 'text/plain')],
                    status=400
                )

            picking = request.env['stock.picking'].sudo().search([
                ('carrier_tracking_ref', '=', tracking_number)
            ], limit=1)

            if not picking:
                _logger.warning("No picking found for tracking_number: %s", tracking_number)
                return request.make_response(
                    'Not found',
                    headers=[('Content-Type', 'text/plain')],
                    status=404
                )

            picking._handle_redbox_status(status_name, data)

            return request.make_response(
                'OK',
                headers=[('Content-Type', 'text/plain')],
                status=200
            )

        except Exception as e:
            _logger.exception("Error processing Redbox webhook")
            return request.make_response(
                'Internal Server Error',
                headers=[('Content-Type', 'text/plain')],
                status=500
            )