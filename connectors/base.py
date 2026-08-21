from abc import ABC, abstractmethod


class PosConnectorBase(ABC):
    """Common interface every POS provider connector must implement.

    A connector talks to one external POS system and normalizes its data
    into the shapes the Odoo models expect, so `venue.sales.session` never
    has to know which provider a session came from.
    """

    provider_code = None

    def __init__(self, connection):
        # `connection` is a `venue.sales.connection` recordset (single record).
        self.connection = connection

    @abstractmethod
    def list_restaurants(self):
        """Return [{'external_id': str, 'name': str}, ...] for every
        restaurant/department reachable through this connection."""

    @abstractmethod
    def fetch_closed_sessions(self, restaurant_external_id, date_from, date_to):
        """Return a list of normalized session dicts for one restaurant,
        with cash sessions closed within [date_from, date_to] (inclusive,
        date objects). Each dict has the keys listed in
        `venue.sales.session._SYNCED_FIELDS`.
        """
