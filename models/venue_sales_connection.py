from odoo import fields, models

from ..connectors.registry import get_connector_class


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
    login = fields.Char(string='Логін', required=True, groups='base.group_system')
    password = fields.Char(string='Пароль', required=True, groups='base.group_system')
    active = fields.Boolean(string='Активне', default=True)
    location_ids = fields.One2many('venue.sales.location', 'connection_id', string='Заклади')

    def action_test_connection(self):
        self.ensure_one()
        connector_class = get_connector_class(self.provider)
        try:
            with connector_class(self) as connector:
                restaurants = connector.list_restaurants()
        except Exception as exc:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': "З'єднання не вдалося",
                    'message': str(exc),
                    'type': 'danger',
                    'sticky': True,
                },
            }
        # active_test=False: location_ids alone would silently skip archived
        # locations, letting a re-test recreate a duplicate row for a place
        # someone deliberately deactivated instead of leaving it alone.
        known_ids = set(self.with_context(active_test=False).location_ids.mapped('external_id'))
        new_ones = [r for r in restaurants if r['external_id'] not in known_ids]
        if new_ones:
            self.env['venue.sales.location'].create([
                {'connection_id': self.id, 'external_id': r['external_id'], 'name': r['name']}
                for r in new_ones
            ])
        message = f"Знайдено {len(restaurants)} заклад(ів): додано {len(new_ones)} нових, {len(restaurants) - len(new_ones)} вже було."
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': "З'єднання успішне",
                'message': message,
                'type': 'success',
                'sticky': True,
                'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'},
            },
        }
