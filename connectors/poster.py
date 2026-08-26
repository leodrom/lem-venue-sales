import logging

import requests

from .base import PosConnectorBase

_logger = logging.getLogger(__name__)

TIMEOUT = 20
TRANSACTIONS_PAGE_SIZE = 1000


class PosterConnector(PosConnectorBase):
    """Poster POS API connector.

    Auth: a single static token (format `<account_id>:<hash>`, generated from
    Poster's Доступ → Інтеграції → "Особиста інтеграція" page), sent as a
    `?token=` query param on every call — there's no login/logout session
    like Syrve's. Poster answers HTTP 200 even on auth/param errors, with an
    `{"error": {...}}` JSON body instead of an HTTP error status (confirmed
    by testing a bad token: HTTP 200, `{"error": {"code": 11, "message":
    "Bad access token"}}`), so every call must check for that key itself
    instead of relying on `raise_for_status()`.

    Request/auth shape confirmed against the official `joinposter/api-php`
    library source (dev.joinposter.com's own docs are a JS SPA that doesn't
    expose text to fetch) — see memory.ai/odoo-staging--venue-sales.md,
    "26.08" section, for the derivation.
    """

    provider_code = 'poster'

    def __init__(self, connection):
        super().__init__(connection)
        self._base = connection.server_url.rstrip('/') + '/api'
        self._session = requests.Session()
        # {(date_from, date_to): [raw transaction dict, ...]}, populated lazily so
        # a multi-restaurant sync run hits transactions.getTransactions (account-wide,
        # not filterable by spot) at most once per date range — mirrors Syrve's OLAP
        # checks-count cache in connectors/syrve.py.
        self._transactions_cache = {}

    def __enter__(self):
        # No session to open — one cheap call now just makes a bad token fail fast
        # with a clean message, matching what venue.sales.connection.action_test_connection()
        # expects (see Syrve's raw-401-traceback bug, fixed 21.08).
        self._get('settings.getAllSettings')
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

    def _get(self, method, **params):
        params['token'] = self.connection.password
        params['format'] = 'json'
        resp = self._session.get(f'{self._base}/{method}', params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        if 'error' in data:
            raise Exception(f"Poster API error {data['error'].get('code')}: {data['error'].get('message')}")
        return data['response']

    def list_restaurants(self):
        spots = self._get('spots.getSpots')
        return [{'external_id': str(s['spot_id']), 'name': s['name']} for s in spots]

    def fetch_closed_sessions(self, restaurant_external_id, date_from, date_to):
        shifts = self._get(
            'finance.getCashShifts',
            spot_id=restaurant_external_id,
            dateFrom=date_from.strftime('%Y%m%d'),
            dateTo=date_to.strftime('%Y%m%d'),
        )
        transactions = self._get_transactions(date_from, date_to)
        receipts_by_shift = self._bin_receipts(shifts, transactions, restaurant_external_id)
        return [self._normalize(s, receipts_by_shift) for s in shifts]

    def _get_transactions(self, date_from, date_to):
        cache_key = (date_from, date_to)
        if cache_key in self._transactions_cache:
            return self._transactions_cache[cache_key]
        out = []
        page = 1
        while True:
            data = self._get(
                'transactions.getTransactions',
                date_from=date_from.isoformat(),
                date_to=date_to.isoformat(),
                per_page=TRANSACTIONS_PAGE_SIZE,
                page=page,
            )
            out.extend(data['data'])
            if page * TRANSACTIONS_PAGE_SIZE >= int(data['count']):
                break
            page += 1
        self._transactions_cache[cache_key] = out
        return out

    @staticmethod
    def _bin_receipts(shifts, transactions, restaurant_external_id):
        """Poster's cash-shift payload has no receipt count or transaction-to-shift
        link of its own (unlike Syrve, where a session id is at least a queryable OLAP
        dimension) — transactions.getTransactions is account-wide, so each receipt is
        bucketed by whichever shift's [date_start, date_end-or-now) window contains its
        date_close. Shifts don't overlap for a given spot, so this is unambiguous.
        Same idea as Syrve's separate OLAP checks-by-session lookup.
        """
        windows = [
            (s['date_start'], s['date_end'] if s['timeend'] != '0' else None, s['cash_shift_id'])
            for s in shifts
        ]
        counts = {s['cash_shift_id']: 0 for s in shifts}
        for t in transactions:
            if str(t.get('spot_id')) != str(restaurant_external_id):
                continue
            close = t.get('date_close')
            if not close:
                continue
            for date_start, date_end, shift_id in windows:
                if date_start <= close and (date_end is None or close <= date_end):
                    counts[shift_id] += 1
                    break
        return counts

    @staticmethod
    def _normalize(raw, receipts_by_shift):
        is_closed = raw['timeend'] != '0'
        sales_cash = int(raw['amount_sell_cash']) / 100
        sales_card = int(raw['amount_sell_card']) / 100
        sales_credit = int(raw['amount_credit']) / 100
        return {
            'external_id': raw['cash_shift_id'],
            'session_number': raw['cash_shift_id'],
            'date_open': raw['date_start'],
            'date_close': raw['date_end'] if is_closed else None,
            # No 'status' key: that field is for provider-specific accounting-review detail
            # (Syrve's accepted/unaccepted/has-warnings) — Poster has no such workflow, and
            # open/closed already has its own universal home in venue.sales.session.shift_state
            # (derived from date_close), so there's nothing honest to put here.
            # Poster has no register/terminal concept anywhere in its API (checked every
            # endpoint) — one fixed name so every Poster location still gets a real,
            # visible row in Заклад → Каси instead of a blank "Каса" column.
            'cash_reg_number': 'Основна',
            'receipts_count': receipts_by_shift.get(raw['cash_shift_id'], 0),
            'sales_cash': sales_cash,
            'sales_card': sales_card,
            'sales_credit': sales_credit,
            # No single "total" field on this endpoint (unlike Syrve's payOrders) —
            # summed from the components above instead of inventing a source for it.
            'total_sales': sales_cash + sales_card + sales_credit,
            'pay_in': int(raw['amount_debit']) / 100,
            'pay_out': int(raw['amount_collection']) / 100,
            'cash_remain': int(raw['amount_end']) / 100,
            # writeoffs/cash_diff/date_accept/fiscal_number: no equivalent in this
            # endpoint's payload (Syrve's cash_diff semantics took a full audit of 169
            # real sessions to pin down — not reproducing that guess here on a hunch).
            # Left as honest absence rather than a fabricated number.
            'raw_data': raw,
        }
