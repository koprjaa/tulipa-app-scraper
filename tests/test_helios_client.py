#
# Project: tulipa-app-scraper
# File:    test_helios_client.py
#
# Description:
# Tests for the Helios HTTP layer: TLS is verified against the pinned CA chain.
#
# Author:
# Jan Alexandr Kopřiva
# jan.alexandr.kopriva@gmail.com
#
# License: MIT
#

"""Unit tests for HeliosClient — RPC calls verify TLS against the pinned CA bundle."""
import ssl

import requests

from tulipa_app_scraper.infrastructure import helios_client
from tulipa_app_scraper.infrastructure.config import Settings


def test_call_verifies_tls_against_pinned_ca_bundle(monkeypatch):
    client = helios_client.HeliosClient(Settings())
    sent = {}

    def fake_post(url, **kwargs):
        sent.update(kwargs)
        raise requests.exceptions.ConnectionError("no network in tests")

    monkeypatch.setattr(client._http, "post", fake_post)

    assert client.call({"_parameters": []}, is_reset_call=True) is None
    assert sent["verify"] == str(helios_client.CA_BUNDLE)
    # The bundle must load as the intermediate + root, not an empty or broken file.
    assert ssl.create_default_context(cafile=sent["verify"]).cert_store_stats()["x509_ca"] == 2
