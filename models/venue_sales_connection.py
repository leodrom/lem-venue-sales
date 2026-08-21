from odoo import fields, models


class PosProviderConnection(models.Model):
    _name = 'venue.sales.connection'
    _description = 'Підключення до POS-провайдера'

    name = fields.Char(string='Назва', required=True)
    provider = fields.Selection(
        [('syrve', 'Syrve')],
        string='Провайдер',
        required=True,
        default='syrve',
        help="Який коннектор (див. connectors/registry.py) обробляє це підключення.",
    )
    server_url = fields.Char(string='Адреса сервера', required=True, help="напр. https://ojakhi-lviv-lem-station.syrve.online")
    login = fields.Char(string='Логін', required=True)
    password = fields.Char(string='Пароль', required=True)
    active = fields.Boolean(string='Активне', default=True)
    location_ids = fields.One2many('venue.sales.location', 'connection_id', string='Заклади')
