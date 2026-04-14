# -*- coding: utf-8 -*-
# from odoo import http


# class RedboxOdoo(http.Controller):
#     @http.route('/redbox_odoo/redbox_odoo', auth='public')
#     def index(self, **kw):
#         return "Hello, world"

#     @http.route('/redbox_odoo/redbox_odoo/objects', auth='public')
#     def list(self, **kw):
#         return http.request.render('redbox_odoo.listing', {
#             'root': '/redbox_odoo/redbox_odoo',
#             'objects': http.request.env['redbox_odoo.redbox_odoo'].search([]),
#         })

#     @http.route('/redbox_odoo/redbox_odoo/objects/<model("redbox_odoo.redbox_odoo"):obj>', auth='public')
#     def object(self, obj, **kw):
#         return http.request.render('redbox_odoo.object', {
#             'object': obj
#         })

