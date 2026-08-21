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
        # sudo(): group_venue_sales_user can trigger a sync but has no access to
        # venue.sales.connection (holds provider credentials) — the connector needs
        # to read login/server_url/password regardless of the calling user's rights.
        connection = self.location_id.connection_id.sudo()
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
