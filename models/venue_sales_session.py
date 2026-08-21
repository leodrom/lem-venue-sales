import json
import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# The 5 statuses Syrve's v2/cashshifts API is known to return (confirmed via
# its own 409 error enum listing). Selection values aren't enforced by the
# ORM on write, so an unknown future status from another provider just shows
# untranslated instead of breaking the sync.
STATUS_SELECTION = [
    ('OPEN', 'Відкрита'),
    ('CLOSED', 'Закрита'),
    ('ACCEPTED', 'Прийнята'),
    ('UNACCEPTED', 'Не прийнята'),
    ('HASWARNINGS', 'Є попередження'),
]


class PosCashSession(models.Model):
    _name = 'venue.sales.session'
    _description = 'Касова зміна POS (Z-звіт)'
    _order = 'date_open desc'
    _restaurant_external_id_uniq = models.Constraint(
        'unique(location_id, external_id)',
        'Цю зміну вже синхронізовано для цього закладу.',
    )

    display_name = fields.Char(compute='_compute_display_name')
    location_id = fields.Many2one('venue.sales.location', string='Заклад', required=True, ondelete='cascade')
    provider = fields.Selection(related='location_id.provider', string='Провайдер', store=True)
    external_id = fields.Char(string='Ідентифікатор у провайдера', required=True, help="Id зміни, як повертає API провайдера.")
    session_number = fields.Integer(string='Номер зміни')
    fiscal_number = fields.Integer(string='Фіскальний номер')
    cash_reg_number = fields.Char(string='Номер каси')
    date = fields.Date(string='Дата', compute='_compute_date', store=True, index=True)
    date_open = fields.Datetime(string='Відкрито')
    date_close = fields.Datetime(string='Закрито')
    date_accept = fields.Datetime(string='Прийнято')
    status = fields.Selection(STATUS_SELECTION, string='Статус')
    receipts_count = fields.Integer(string='Чеків')
    sales_cash = fields.Monetary(string='Готівкою', currency_field='currency_id')
    sales_card = fields.Monetary(string='Карткою', currency_field='currency_id')
    sales_credit = fields.Monetary(string='В кредит', currency_field='currency_id')
    total_sales = fields.Monetary(string='Всього', currency_field='currency_id')
    # aggregator='avg' here only controls what the client REQUESTS ('avg_check:avg' in
    # web_read_group's aggregates) — the actual number shown is replaced in web_read_group()
    # below with the true weighted average (sum(total_sales)/sum(receipts_count)), because a
    # single-field SQL aggregator can't express a ratio of two other fields' sums.
    avg_check = fields.Monetary(string='Середній чек', compute='_compute_avg_check', store=True,
                                 currency_field='currency_id', aggregator='avg')
    writeoffs = fields.Monetary(string='Списання', currency_field='currency_id')
    pay_in = fields.Monetary(string='Внесення готівки', currency_field='currency_id')
    pay_out = fields.Monetary(string='Вилучення готівки', currency_field='currency_id')
    cash_diff = fields.Monetary(string='Різниця в касі', currency_field='currency_id')
    cash_remain = fields.Monetary(string='Залишок у касі', currency_field='currency_id')
    currency_id = fields.Many2one('res.currency', string='Валюта', default=lambda self: self.env.ref('base.UAH'))
    raw_data = fields.Text(string='Сирі дані', help="Оригінальний нормалізований payload від коннектора, для діагностики.")

    @api.depends('date_open')
    def _compute_date(self):
        for rec in self:
            rec.date = rec.date_open.date() if rec.date_open else False

    @api.depends('total_sales', 'receipts_count')
    def _compute_avg_check(self):
        for rec in self:
            rec.avg_check = rec.total_sales / rec.receipts_count if rec.receipts_count else 0

    @api.depends('session_number', 'cash_reg_number', 'location_id.name', 'date_open')
    def _compute_display_name(self):
        for rec in self:
            date_str = fields.Date.to_string(rec.date_open) if rec.date_open else ''
            rec.display_name = f"Зміна №{rec.session_number}, каса {rec.cash_reg_number} — {rec.location_id.name} ({date_str})"

    @api.model
    def web_read_group(self, domain, groupby, aggregates=(), limit=None, offset=0, order=None, **kwargs):
        aggregates = list(aggregates)
        avg_check_specs = [a for a in aggregates if a.split(':')[0] == 'avg_check']
        extra = [f for f in ('total_sales:sum', 'receipts_count:sum')
                 if avg_check_specs and f not in aggregates]
        result = super().web_read_group(
            domain, groupby, aggregates + extra, limit=limit, offset=offset, order=order, **kwargs)
        if avg_check_specs:
            for group in result['groups']:
                total = group.get('total_sales:sum') or 0
                count = group.get('receipts_count:sum') or 0
                weighted_avg = total / count if count else 0
                for spec in avg_check_specs:
                    group[spec] = weighted_avg
                for f in extra:
                    group.pop(f, None)
        return result

    def _sync_restaurant(self, connector, restaurant, date_from, date_to):
        sessions = connector.fetch_closed_sessions(restaurant.external_id, date_from, date_to)
        for session in sessions:
            self._upsert_session(restaurant, session)
        _logger.info("Synced %d session(s) for %s", len(sessions), restaurant.display_name)
        return len(sessions)

    def _upsert_session(self, restaurant, data):
        vals = {
            'location_id': restaurant.id,
            'external_id': data['external_id'],
            'session_number': data.get('session_number'),
            'fiscal_number': data.get('fiscal_number'),
            'cash_reg_number': data.get('cash_reg_number'),
            'date_open': self._parse_provider_datetime(data.get('date_open')),
            'date_close': self._parse_provider_datetime(data.get('date_close')),
            'date_accept': self._parse_provider_datetime(data.get('date_accept')),
            'status': data.get('status'),
            'receipts_count': data.get('receipts_count') or 0,
            'sales_cash': data.get('sales_cash') or 0,
            'sales_card': data.get('sales_card') or 0,
            'sales_credit': data.get('sales_credit') or 0,
            'total_sales': data.get('total_sales') or 0,
            'writeoffs': data.get('writeoffs') or 0,
            'pay_in': data.get('pay_in') or 0,
            'pay_out': data.get('pay_out') or 0,
            'cash_diff': data.get('cash_diff') or 0,
            'cash_remain': data.get('cash_remain') or 0,
            'raw_data': json.dumps(data.get('raw_data'), ensure_ascii=False, default=str),
        }
        existing = self.search([
            ('location_id', '=', restaurant.id),
            ('external_id', '=', data['external_id']),
        ], limit=1)
        if existing:
            existing.write(vals)
        else:
            self.create(vals)

    @staticmethod
    def _parse_provider_datetime(value):
        if not value:
            return False
        # Syrve returns e.g. "2026-08-18T09:38:42.021" (naive, local time, no tz).
        return fields.Datetime.to_datetime(value.split('.')[0].replace('T', ' '))
