from odoo import fields, models


class VenueSalesRegister(models.Model):
    _name = 'venue.sales.register'
    _description = 'Каса'
    _uniq = models.Constraint(
        'unique(location_id, name)',
        'Ця каса вже існує для цього закладу.',
    )

    location_id = fields.Many2one('venue.sales.location', string='Заклад', required=True, ondelete='cascade')
    name = fields.Char(string='Назва/номер', required=True)
    is_fiscal = fields.Boolean(
        string='Фіскальна каса', default=True,
        help="Знято — каса вважається службовою (інкасація/адмін, не реальна точка "
             "продажу) і її зміни приховані зі звітів за замовчуванням.",
    )
