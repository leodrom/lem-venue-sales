from datetime import timedelta

from odoo import fields, models

from ..connectors.registry import get_connector_class


class PosRestaurantSyncWizard(models.TransientModel):
    _name = 'venue.sales.sync.wizard'
    _description = 'Завантаження даних POS за період'

    location_id = fields.Many2one('venue.sales.location', string='Заклад', required=True, readonly=True)
    date_from = fields.Date(string='З дати', required=True,
                             default=lambda self: fields.Date.today() - timedelta(days=7))
    date_to = fields.Date(string='По дату', required=True, default=fields.Date.today)

    def action_sync(self):
        self.ensure_one()
        connection = self.location_id.connection_id
        connector_class = get_connector_class(connection.provider)
        try:
            with connector_class(connection) as connector:
                count = self.env['venue.sales.session']._sync_restaurant(
                    connector, self.location_id, self.date_from, self.date_to)
        except Exception as exc:
            self.location_id._log_sync_result(False, error=str(exc))
            raise
        self.location_id._log_sync_result(True, count=count)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Дані завантажено',
                'message': f"Синхронізовано {count} зміну(и) за період {self.date_from} — {self.date_to}",
                'type': 'success',
                'sticky': False,
            },
        }
