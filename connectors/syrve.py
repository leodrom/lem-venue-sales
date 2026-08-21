import hashlib
import logging
import xml.etree.ElementTree as ET

import requests

from .base import PosConnectorBase

_logger = logging.getLogger(__name__)

TIMEOUT = 20


class SyrveConnector(PosConnectorBase):
    """Syrve (legacy RMS) API connector.

    Auth: GET /resto/api/auth?login=...&pass=sha1(password) -> session key,
    reused as a `?key=` query param on every subsequent call, closed with
    /resto/api/logout when the connector is done. See
    memory.ai/syrve--README.md for the full endpoint map this was derived
    from.
    """

    provider_code = 'syrve'

    def __init__(self, connection):
        super().__init__(connection)
        self._base = connection.server_url.rstrip('/') + '/resto/api'
        self._session = requests.Session()
        self._token = None
        # {(date_from, date_to): {session_id: checks_count}}, populated
        # lazily so we hit the OLAP report at most once per sync run
        # (it returns account-wide data, not filterable by department).
        self._checks_cache = {}

    def __enter__(self):
        self._login()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._logout()

    def _login(self):
        pwd_hash = hashlib.sha1(self.connection.password.encode()).hexdigest()
        resp = self._session.get(
            f'{self._base}/auth',
            params={'login': self.connection.login, 'pass': pwd_hash},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        self._token = resp.text.strip()

    def _logout(self):
        if not self._token:
            return
        try:
            self._session.get(f'{self._base}/logout', params={'key': self._token}, timeout=TIMEOUT)
        except requests.RequestException:
            _logger.warning("Syrve logout failed for connection %s", self.connection.display_name, exc_info=True)
        self._token = None

    def _get(self, path, **params):
        params['key'] = self._token
        resp = self._session.get(f'{self._base}/{path}', params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp

    def list_restaurants(self):
        data = self._get('corporation/departments').text
        # XML: <corporateItemDto><id>..</id><name>..</name><type>DEPARTMENT</type>...
        root = ET.fromstring(data)
        out = []
        for item in root.findall('corporateItemDto'):
            if item.findtext('type') == 'DEPARTMENT':
                out.append({
                    'external_id': item.findtext('id'),
                    'name': item.findtext('name'),
                })
        return out

    def fetch_closed_sessions(self, restaurant_external_id, date_from, date_to):
        resp = self._get(
            'v2/cashshifts/list',
            openDateFrom=date_from.isoformat(),
            openDateTo=date_to.isoformat(),
            status='ANY',
            departmentId=restaurant_external_id,
        )
        sessions = resp.json()
        checks_by_session = self._get_checks_by_session(date_from, date_to)
        return [self._normalize(s, checks_by_session) for s in sessions]

    def _get_checks_by_session(self, date_from, date_to):
        """Number of receipts (Syrve's "Чеков"/UniqOrderId OLAP measure),
        grouped by cash session. cashshifts/list has no check-count field
        of its own, so this is a second call against the OLAP report,
        grouping the SALES report by SessionID. Cash-management-only
        sessions (pure pay-in/pay-out, no orders) simply don't appear
        here, which is correct — they had 0 checks.
        """
        cache_key = (date_from, date_to)
        if cache_key in self._checks_cache:
            return self._checks_cache[cache_key]
        resp = self._get(
            'reports/olap',
            report='SALES',
            groupRow='SessionID',
            agr='UniqOrderId',
            **{'from': date_from.strftime('%d.%m.%Y'), 'to': date_to.strftime('%d.%m.%Y')},
        )
        root = ET.fromstring(resp.text)
        result = {}
        for row in root.findall('r'):
            session_id = row.findtext('SessionID')
            checks = row.findtext('UniqOrderId')
            if session_id and checks is not None:
                result[session_id] = int(float(checks))
        self._checks_cache[cache_key] = result
        return result

    @staticmethod
    def _normalize(raw, checks_by_session):
        return {
            'external_id': raw['id'],
            'session_number': raw.get('sessionNumber'),
            'fiscal_number': raw.get('fiscalNumber'),
            'date_open': raw.get('openDate'),
            'date_close': raw.get('closeDate'),
            'date_accept': raw.get('acceptDate'),
            'status': raw.get('sessionStatus'),
            'cash_reg_number': str(raw['cashRegNumber']) if raw.get('cashRegNumber') is not None else False,
            'sales_cash': raw.get('salesCash') or 0,
            'sales_card': raw.get('salesCard') or 0,
            'sales_credit': raw.get('salesCredit') or 0,
            'total_sales': raw.get('payOrders') or 0,
            'writeoffs': raw.get('sumWriteoffOrders') or 0,
            'pay_in': raw.get('payIn') or 0,
            'pay_out': raw.get('payOut') or 0,
            'cash_diff': raw.get('cashDiff') or 0,
            'cash_remain': raw.get('cashRemain') or 0,
            'receipts_count': checks_by_session.get(raw['id'], 0),
            'raw_data': raw,
        }
