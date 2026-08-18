import hmac
import json
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class RedboxWebhookController(http.Controller):

    @http.route(
        '/redbox/webhook',
        type='http',
        auth='public',
        methods=['POST'],
        csrf=False,
    )
    def redbox_webhook(self, **kwargs):
        try:
            # ---------------------------------------------------------
            # 1. Get webhook token from header
            # ---------------------------------------------------------
            webhook_token = request.httprequest.headers.get('redbox-webhook-token', '')

            if not webhook_token:
                _logger.warning(
                    "Redbox webhook: missing token"
                )
                return request.make_response(
                    'Unauthorized',
                    headers=[('Content-Type', 'text/plain')],
                    status=401,
                )

            # ---------------------------------------------------------
            # 2. Parse request body
            # ---------------------------------------------------------
            raw_data = request.httprequest.data

            try:
                data = json.loads(raw_data.decode('utf-8'))
            except (UnicodeDecodeError, json.JSONDecodeError):
                _logger.warning(
                    "Redbox webhook: invalid JSON"
                )
                return request.make_response(
                    'Invalid JSON',
                    headers=[('Content-Type', 'text/plain')],
                    status=400,
                )

            _logger.info(
                "Redbox webhook received: tracking_number=%s, status_name=%s",
                data.get('tracking_number'),
                data.get('status_name'),
            )

            # ---------------------------------------------------------
            # 3. Validate required fields
            # ---------------------------------------------------------
            tracking_number = data.get('tracking_number')
            status_name = data.get('status_name')

            if not tracking_number:
                return request.make_response(
                    'Missing tracking_number',
                    headers=[('Content-Type', 'text/plain')],
                    status=400,
                )

            if not status_name:
                return request.make_response(
                    'Missing status_name',
                    headers=[('Content-Type', 'text/plain')],
                    status=400,
                )

            # ---------------------------------------------------------
            # 4. Resolve carrier by webhook token
            # ---------------------------------------------------------
            Carrier = request.env['delivery.carrier'].sudo()

            carriers = Carrier.search([
                ('delivery_type', '=', 'redbox'),
            ])

            carrier = False

            for candidate in carriers:
                candidate_token = candidate.redbox_webhook_token or ''

                if hmac.compare_digest(
                    candidate_token,
                    webhook_token,
                ):
                    carrier = candidate
                    break

            if not carrier:
                _logger.warning(
                    "Redbox webhook: invalid token"
                )
                return request.make_response(
                    'Unauthorized',
                    headers=[('Content-Type', 'text/plain')],
                    status=401,
                )

            # ---------------------------------------------------------
            # 5. Find picking
            # ---------------------------------------------------------
            Picking = request.env['stock.picking'].sudo()

            picking = Picking.search([
                ('carrier_id', '=', carrier.id),
                ('carrier_tracking_ref', '=', tracking_number),
            ], limit=1)

            if not picking:
                _logger.warning(
                    "Redbox webhook: no picking found "
                    "tracking_number=%s carrier_id=%s",
                    tracking_number,
                    carrier.id,
                )

                return request.make_response(
                    'Not found',
                    headers=[('Content-Type', 'text/plain')],
                    status=404,
                )

            # ---------------------------------------------------------
            # 6. Handle Redbox status
            # ---------------------------------------------------------
            picking._handle_redbox_status(
                status_name,
                data,
            )

            # ---------------------------------------------------------
            # 7. Success
            # ---------------------------------------------------------
            return request.make_response(
                'OK',
                headers=[('Content-Type', 'text/plain')],
                status=200,
            )

        except Exception:
            _logger.exception(
                "Error processing Redbox webhook"
            )

            return request.make_response(
                'Internal Server Error',
                headers=[('Content-Type', 'text/plain')],
                status=500,
            )