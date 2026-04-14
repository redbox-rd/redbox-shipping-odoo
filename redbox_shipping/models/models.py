# -*- coding: utf-8 -*-

# from odoo import models, fields, api


# class redbox_odoo(models.Model):
#     _name = 'redbox_odoo.redbox_odoo'
#     _description = 'redbox_odoo.redbox_odoo'

#     name = fields.Char()
#     value = fields.Integer()
#     value2 = fields.Float(compute="_value_pc", store=True)
#     description = fields.Text()
#
#     @api.depends('value')
#     def _value_pc(self):
#         for record in self:
#             record.value2 = float(record.value) / 100

