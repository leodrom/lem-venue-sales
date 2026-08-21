{
    'name': 'Продажі закладів',
    'version': '1.0',
    'summary': "Збір даних продажів (Z-звіти) з POS-систем закладів мережі (Syrve, Poster, ...)",
    'description': """
Провайдер-агностичний збір даних продажів з різних POS-систем закладів.

Кожен заклад (`venue.sales.location`) підключений до провайдера через
`venue.sales.connection`. Щоденний cron тягне закриті касові зміни
(Z-звіти) кожного провайдера через відповідний коннектор і зберігає їх
у `venue.sales.session`, незалежно від того, який саме POS видав дані.

Підтримувані провайдери: Syrve (legacy RMS API). Poster — заплановано,
ще не реалізовано.
""",
    'category': 'Accounting/Accounting',
    'application': True,
    'depends': ['base', 'mail'],
    'data': [
        'security/venue_sales_security.xml',
        'security/ir.model.access.csv',
        'data/ir_cron.xml',
        'views/venue_sales_session_views.xml',
        'views/venue_sales_location_views.xml',
        'views/venue_sales_sync_wizard_views.xml',
        'views/venue_sales_connection_views.xml',
        'views/menu.xml',
    ],
    'auto_install': False,
    'license': 'LGPL-3',
}
