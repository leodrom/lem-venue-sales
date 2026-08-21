import logging
from datetime import timedelta

from odoo import api, fields, models

from ..connectors.registry import get_connector_class

_logger = logging.getLogger(__name__)

# "Про всяк випадок" — how many days back a scheduled auto-sync re-checks, in
# case a previous day's session was accepted/edited late (see syrve--README.md).
AUTO_SYNC_LOOKBACK_DAYS = 5
AUTO_SYNC_MAX_ATTEMPTS = 5
AUTO_SYNC_RETRY_INTERVAL = timedelta(minutes=30)

# Selection values are hours in the timezone of the ir.cron's configured user
# (see data/ir_cron.xml — user_id must be set to a real user with a real tz,
# not the tz-less superuser, or this silently falls back to UTC).
AUTO_SYNC_HOUR_SELECTION = [(str(h), f'{h:02d}:00') for h in range(24)]


class PosRestaurant(models.Model):
    _name = 'venue.sales.location'
    _inherit = ['avatar.mixin', 'mail.thread']
    _description = 'Заклад (POS)'
    _connection_external_id_uniq = models.Constraint(
        'unique(connection_id, external_id)',
        'Цей заклад уже зареєстровано для цього підключення.',
    )

    name = fields.Char(string='Назва', required=True)
    connection_id = fields.Many2one('venue.sales.connection', string='Підключення', required=True, ondelete='cascade')
    provider = fields.Selection(related='connection_id.provider', string='Провайдер', store=True)
    external_id = fields.Char(
        string='Ідентифікатор у провайдера',
        required=True,
        help="Id закладу/департаменту в системі провайдера "
             "(напр. Syrve corporation/departments id).",
    )
    active = fields.Boolean(string='Активний', default=True)
    session_ids = fields.One2many('venue.sales.session', 'location_id', string='Касові зміни')
    session_count = fields.Integer(string='Кількість змін', compute='_compute_session_count')

    auto_sync_hour = fields.Selection(
        AUTO_SYNC_HOUR_SELECTION, string='Година автозавантаження', default='3',
        help="Щодня система намагатиметься автоматично довантажити дані за "
             "останні 5 днів о цю годину (за вашим місцевим часом). Якщо "
             "спроба невдала — ще 5 спроб з інтервалом 30 хв. Порожньо = "
             "автозавантаження вимкнено для цього закладу.",
    )
    last_auto_sync_date = fields.Date(string='Остання успішна автосинхронізація', readonly=True, copy=False)
    auto_sync_attempt_count = fields.Integer(string='Спроб сьогодні', readonly=True, copy=False, default=0)
    last_auto_sync_attempt = fields.Datetime(string='Час останньої спроби', readonly=True, copy=False)
    last_auto_sync_error = fields.Text(string='Помилка останньої спроби', readonly=True, copy=False)

    def _compute_session_count(self):
        groups = self.env['venue.sales.session']._read_group(
            [('location_id', 'in', self.ids)], ['location_id'], ['__count'],
        )
        counts = {restaurant.id: count for restaurant, count in groups}
        for rec in self:
            rec.session_count = counts.get(rec.id, 0)

    def action_open_sync_wizard(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Завантажити дані',
            'res_model': 'venue.sales.sync.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_location_id': self.id},
        }

    @api.model
    def _cron_auto_sync_restaurants(self):
        now = fields.Datetime.now()
        # auto_sync_hour is entered/read in the cron user's local timezone (see
        # data/ir_cron.xml — user_id must point to a real user with a real tz set,
        # not the tz-less superuser default, or this silently falls back to UTC).
        local_now = fields.Datetime.context_timestamp(self, now)
        today = local_now.date()
        restaurants = self.search([('auto_sync_hour', '!=', False), ('active', '=', True)])
        for restaurant in restaurants:
            if restaurant.last_auto_sync_date == today:
                continue  # already succeeded today

            # A retry cycle is "in progress" purely based on attempt count, not calendar
            # date — a cycle started at e.g. 23:00 legitimately keeps retrying every 30 min
            # past midnight without being mistaken for a fresh day's cycle. attempt_count is
            # reset to 0 on success (see _attempt_auto_sync), so 0 always means "no unresolved
            # cycle", whether that's because none ever started or because the last one
            # succeeded.
            cycle_in_progress = 0 < restaurant.auto_sync_attempt_count < AUTO_SYNC_MAX_ATTEMPTS
            if cycle_in_progress:
                if now - restaurant.last_auto_sync_attempt < AUTO_SYNC_RETRY_INTERVAL:
                    continue  # too soon for the next retry
                next_attempt_number = restaurant.auto_sync_attempt_count + 1
            else:
                if local_now.hour != int(restaurant.auto_sync_hour):
                    continue  # not the scheduled hour, and no retry cycle currently active
                next_attempt_number = 1

            restaurant._attempt_auto_sync(now, today, next_attempt_number)

    def _attempt_auto_sync(self, now, today, attempt_number):
        self.ensure_one()
        self.write({
            'auto_sync_attempt_count': attempt_number,
            'last_auto_sync_attempt': now,
        })
        try:
            connector_class = get_connector_class(self.connection_id.provider)
            date_from = today - timedelta(days=AUTO_SYNC_LOOKBACK_DAYS)
            with connector_class(self.connection_id) as connector:
                count = self.env['venue.sales.session']._sync_restaurant(connector, self, date_from, today)
        except Exception as exc:
            _logger.exception("Auto-sync attempt %d/%d failed for %s",
                               attempt_number, AUTO_SYNC_MAX_ATTEMPTS, self.display_name)
            self.write({'last_auto_sync_error': str(exc)})
            self._log_sync_result(False, attempt_number=attempt_number, error=str(exc))
        else:
            # Reset the counter so a future calendar day starts a genuinely fresh cycle
            # instead of "cycle_in_progress" mistaking leftover state for an active retry.
            self.write({
                'last_auto_sync_date': today,
                'last_auto_sync_error': False,
                'auto_sync_attempt_count': 0,
            })
            self._log_sync_result(True, attempt_number=attempt_number, count=count)

    def _log_sync_result(self, success, attempt_number=None, count=None, error=None):
        self.ensure_one()
        prefix = f"Автозавантаження, спроба {attempt_number}/{AUTO_SYNC_MAX_ATTEMPTS}: " \
            if attempt_number else "Ручне завантаження: "
        if success:
            self.message_post(body=f"{prefix}✅ успішно, синхронізовано {count} зміну(и).")
        else:
            self.message_post(body=f"{prefix}❌ не вдалося: {error}")
