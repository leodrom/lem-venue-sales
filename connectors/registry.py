from .syrve import SyrveConnector

CONNECTORS = {
    SyrveConnector.provider_code: SyrveConnector,
}


def get_connector_class(provider_code):
    try:
        return CONNECTORS[provider_code]
    except KeyError:
        raise NotImplementedError(f"No POS connector implemented for provider '{provider_code}'")
