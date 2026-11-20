import json
from unittest.mock import MagicMock, patch

from odoo.exceptions import UserError
from odoo.tests import HttpCase, TransactionCase, tagged

REQUEST_PATH = 'odoo.addons.redbox_shipping.models.delivery_carrier.requests.request'
LABEL_PATH = 'odoo.addons.redbox_shipping.models.stock_picking.requests.get'


def _response(status=200, data=None):
    res = MagicMock()
    res.status_code = status
    res.json.return_value = data if data is not None else {'success': True}
    return res


class RedboxCommon(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.mock_request = MagicMock(return_value=_response())
        cls.startClassPatcher(patch(REQUEST_PATH, cls.mock_request))
        cls.startClassPatcher(patch(LABEL_PATH, side_effect=Exception("no network in tests")))

        cls.delivery_product = cls.env['product.product'].create({
            'name': 'RedBox Delivery', 'type': 'service'})
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'RedBox',
            'delivery_type': 'redbox',
            'product_id': cls.delivery_product.id,
            'redbox_api_key': 'test-key',
            'redbox_fixed_price': 10.0,
        })
        cls.product = cls.env['product.product'].create({
            'name': 'Shoes', 'type': 'consu', 'default_code': 'SH-1', 'list_price': 100.0})
        cls.partner = cls.env['res.partner'].create({
            'name': 'Customer', 'phone': '+966500000000', 'street': 'King Fahd Rd',
            'city': 'Riyadh', 'email': 'customer@example.com',
            'country_id': cls.env.ref('base.sa').id,
        })

    def _make_order(self):
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'carrier_id': self.carrier.id,
            'order_line': [(0, 0, {
                'product_id': self.product.id, 'product_uom_qty': 2, 'price_unit': 100.0})],
        })
        order.action_confirm()
        picking = order.picking_ids.filtered(lambda p: p.picking_type_code == 'outgoing')
        self.assertEqual(picking.carrier_id, self.carrier)
        return order, picking

    def _set_done_qty(self, picking):
        for move in picking.move_ids:
            move.quantity = move.product_uom_qty
            if 'picked' in move._fields:
                move.picked = True


@tagged('post_install', '-at_install')
class TestRedboxShipping(RedboxCommon):

    def test_payload_and_cod_allowed(self):
        self.carrier.redbox_allow_cod = True
        order, picking = self._make_order()
        self._set_done_qty(picking)
        payload = picking._redbox_prepare_shipment_payload()
        self.assertEqual(payload['reference'], order.name)
        self.assertEqual(payload['customer_city'], 'Riyadh')
        self.assertEqual(payload['customer_phone'], '+966500000000')
        self.assertEqual(payload['cod_amount'], order.amount_total)
        self.assertEqual(payload['items'][0]['sku'], 'SH-1')
        self.assertEqual(payload['items'][0]['quantity'], 2)
        self.assertNotIn('delivery_method', payload)

    def test_cod_blocked_unpaid_raises(self):
        self.carrier.redbox_allow_cod = False
        _order, picking = self._make_order()
        with self.assertRaises(UserError):
            picking._redbox_prepare_shipment_payload()

    def test_cod_blocked_paid_sends_zero(self):
        self.carrier.redbox_allow_cod = False
        order, picking = self._make_order()
        with patch.object(type(picking), '_redbox_get_order_paid_amount',
                          lambda self, o: o.amount_total):
            payload = picking._redbox_prepare_shipment_payload()
        self.assertEqual(payload['cod_amount'], 0.0)

    def test_send_shipping_once(self):
        self.carrier.redbox_allow_cod = True
        _order, picking = self._make_order()
        self._set_done_qty(picking)
        self.mock_request.reset_mock()
        self.mock_request.return_value = _response(data={
            'success': True, 'shipment_id': 'SHP1', 'tracking_number': 'TRK1',
            'shipping_label_url': 'https://example.com/label.pdf'})
        self.carrier.send_shipping(picking)
        self.assertEqual(picking.redbox_shipment_id, 'SHP1')
        self.assertEqual(picking.carrier_tracking_ref, 'TRK1')
        method, url = self.mock_request.call_args[0][:2]
        self.assertEqual(method, 'POST')
        self.assertEqual(url, 'https://stage.api.redboxsa.com/v3/shipments')
        # second call must not create another shipment
        self.mock_request.reset_mock()
        self.carrier.send_shipping(picking)
        self.mock_request.assert_not_called()

    def test_409_adopts_recent_pending_shipment(self):
        """Shipment created on RedBox but Odoo rolled back: adopt it, don't duplicate."""
        self.carrier.redbox_allow_cod = True
        order, picking = self._make_order()
        self.mock_request.reset_mock()
        self.mock_request.side_effect = [
            _response(409, {'success': False, 'msg': 'Conflict: Shipment is existed'}),
            _response(data={'success': True, 'shipments': [
                {'id': 'OTHER', 'reference': order.name, 'status_name': 'Delivered',
                 'customer': {'phone': '+966500000000'}},
                # same reference/phone/Pending but different COD: another database's order
                {'id': 'OTHERDB', 'reference': order.name, 'status_name': 'Pending',
                 'customer': {'phone': '+966500000000'}, 'cod': {'amount': 1.0}},
                {'id': 'SHPX', 'tracking_number': 'TRKX', 'reference': order.name,
                 'status_name': 'Pending', 'is_return': False,
                 'customer': {'phone': '+966500000000'},
                 'cod': {'amount': order.amount_total, 'currency': 'SAR'}},
            ]}),
            _response(data={'success': True, 'url': 'https://example.com/x.pdf'}),
        ]
        try:
            self.carrier.send_shipping(picking)
        finally:
            self.mock_request.side_effect = None
        self.assertEqual(picking.redbox_shipment_id, 'SHPX')
        self.assertEqual(picking.carrier_tracking_ref, 'TRKX')
        self.assertEqual(picking.redbox_reference, order.name)
        self.assertEqual(self.mock_request.call_count, 3)

    def test_409_collision_retries_with_unique_reference(self):
        self.carrier.redbox_allow_cod = True
        order, picking = self._make_order()
        self.mock_request.reset_mock()
        self.mock_request.side_effect = [
            _response(409, {'success': False, 'msg': 'Conflict: Shipment is existed'}),
            _response(data={'success': True, 'shipments': []}),
            _response(data={'success': True, 'shipment_id': 'SHPN', 'tracking_number': 'TRKN'}),
        ]
        try:
            self.carrier.send_shipping(picking)
        finally:
            self.mock_request.side_effect = None
        expected_ref = '%s-P%s' % (order.name, picking.id)
        self.assertEqual(self.mock_request.call_args_list[2][1]['json']['reference'], expected_ref)
        self.assertEqual(picking.redbox_reference, expected_ref)
        self.assertEqual(picking.redbox_shipment_id, 'SHPN')

    def test_api_error_raises(self):
        self.carrier.redbox_allow_cod = True
        _order, picking = self._make_order()
        self.mock_request.return_value = _response(400, {'success': False, 'msg': 'Bad phone'})
        with self.assertRaisesRegex(UserError, 'Bad phone'):
            self.carrier.send_shipping(picking)

    def test_status_dedup(self):
        _order, picking = self._make_order()
        picking.write({'redbox_shipment_id': 'SHP1', 'carrier_tracking_ref': 'TRK1'})
        base = {'shipment_id': 'SHP1', 'tracking_number': 'TRK1'}
        self.assertEqual(picking._redbox_apply_status(dict(
            base, status_name='Deposited', status_code=501, date='2026-01-02T10:00:00.000Z')), 'ok')
        self.assertEqual(picking.redbox_shipment_status, 'Deposited')
        # older event is ignored
        self.assertEqual(picking._redbox_apply_status(dict(
            base, status_name='In-Transit', status_code=302, date='2026-01-01T10:00:00.000Z')), 'stale')
        self.assertEqual(picking.redbox_shipment_status, 'Deposited')
        # replay is ignored
        self.assertEqual(picking._redbox_apply_status(dict(
            base, status_name='Deposited', status_code=501, date='2026-01-02T10:00:00.000Z')), 'duplicate')

    def test_blocked_providers(self):
        provider = self.env['payment.provider'].create({'name': 'Cash on Delivery (test)'})
        self.carrier.blocked_payment_provider_ids = provider
        order, _picking = self._make_order()
        Provider = self.env['payment.provider']
        filtered = Provider._redbox_filter_providers(provider, order.id)
        self.assertFalse(filtered)
        self.assertEqual(Provider._redbox_filter_providers(provider, False), provider)

    def test_return_flow(self):
        self.carrier.redbox_allow_cod = True
        _order, picking = self._make_order()
        # Shipment created by an older module version: tracking only, no shipment_id
        picking.write({'carrier_tracking_ref': 'TRK1', 'redbox_reference': 'X'})
        self._set_done_qty(picking)
        picking.with_context(skip_sms=True).button_validate()
        self.assertEqual(picking.state, 'done')

        return_type = picking.picking_type_id.return_picking_type_id or \
            self.env.ref('stock.picking_type_in')
        out_move = picking.move_ids[0]
        move_vals = {
            'product_id': self.product.id,
            'product_uom_qty': 2,
            'product_uom': out_move.product_uom.id,
            'location_id': picking.location_dest_id.id,
            'location_dest_id': picking.location_id.id,
            'origin_returned_move_id': out_move.id,
        }
        if 'name' in self.env['stock.move']._fields:  # removed in Odoo 19
            move_vals['name'] = self.product.name
        return_picking = self.env['stock.picking'].create({
            'picking_type_id': return_type.id,
            'partner_id': self.partner.id,
            'location_id': picking.location_dest_id.id,
            'location_dest_id': picking.location_id.id,
            'move_ids': [(0, 0, move_vals)],
        })
        return_picking.action_confirm()
        self.assertTrue(return_picking.redbox_can_create_return)

        self.mock_request.reset_mock()
        self.mock_request.side_effect = [
            _response(data={'success': True, 'shipment': {'id': 'SHP1'}}),
            _response(data={'success': True, 'shipment_id': 'RSHP1', 'tracking_number': 'RTRK1',
                            'shipping_label_url': False, 'return_code': 'RC123'}),
        ]
        try:
            return_picking.action_redbox_create_return()
        finally:
            self.mock_request.side_effect = None
        self.assertEqual(picking.redbox_shipment_id, 'SHP1')
        calls = self.mock_request.call_args_list
        self.assertTrue(calls[1][0][1].endswith('/v3/shipments/returns'))
        self.assertEqual(calls[1][1]['json']['original_shipment_id'], 'SHP1')
        self.assertEqual(return_picking.carrier_tracking_ref, 'RTRK1')
        self.assertEqual(return_picking.redbox_return_code, 'RC123')
        self.assertEqual(return_picking.carrier_id, self.carrier)
        self.assertFalse(return_picking.redbox_can_create_return)

        return_picking._redbox_apply_status({
            'shipment_id': 'RSHP1', 'tracking_number': 'RTRK1',
            'status_name': 'Returned', 'status_code': 1101, 'date': '2026-01-05T10:00:00Z'})
        self.assertEqual(return_picking.state, 'done')


@tagged('post_install', '-at_install')
class TestRedboxWebhook(HttpCase):

    def setUp(self):
        super().setUp()
        patcher = patch(REQUEST_PATH, return_value=_response())
        patcher.start()
        self.addCleanup(patcher.stop)
        product = self.env['product.product'].create({'name': 'RB', 'type': 'service'})
        self.carrier = self.env['delivery.carrier'].create({
            'name': 'RedBox WH', 'delivery_type': 'redbox', 'product_id': product.id})

    def _post(self, data, token):
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['redbox-webhook-token'] = token
        return self.url_open('/redbox/webhook', data=json.dumps(data), headers=headers)

    def test_webhook_auth_and_unknown(self):
        payload = {'shipment_id': 'NOPE', 'tracking_number': 'NOPE', 'status_name': 'Delivered'}
        self.assertEqual(self._post(payload, None).status_code, 401)
        self.assertEqual(self._post(payload, 'wrong').status_code, 401)
        self.assertEqual(self._post(payload, self.carrier.redbox_webhook_token).status_code, 200)
