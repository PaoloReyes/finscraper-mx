import json
from decimal import Decimal

import pytest

from finscraper_mx import PROVIDERS, AccountKind, AuthRequired, Balance, Provider, get_provider
from finscraper_mx.cli import main


class Fake(Provider):
    id = "fake"
    name = "Fake"
    auth_fields = ("token",)

    def fetch_balances(self):
        return [Balance("fake", "Cuenta *0001", AccountKind.CHECKING, Decimal("1234.50"))]


def test_missing_auth_is_rejected():
    with pytest.raises(AuthRequired, match="token"):
        Fake()


def test_balance_serializes_amount_as_string():
    balance = Fake(token="x").fetch_balances()[0]
    assert balance.to_dict()["amount"] == "1234.50"
    assert balance.to_dict()["kind"] == "checking"


def test_registry_ids_match_provider_ids():
    assert all(id == provider.id for id, provider in PROVIDERS.items())
    with pytest.raises(KeyError, match="unknown institution"):
        get_provider("nope")


def test_cli_balances(tmp_path, monkeypatch, capsys):
    monkeypatch.setitem(PROVIDERS, "fake", Fake)
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"token": "x"}))
    assert main(["balances", "fake", "--auth", str(auth)]) == 0
    assert json.loads(capsys.readouterr().out)[0]["account"] == "Cuenta *0001"


def test_cli_reports_missing_auth(tmp_path, capsys):
    auth = tmp_path / "auth.json"
    auth.write_text("{}")
    assert main(["balances", "bbva_mx", "--auth", str(auth)]) == 1
    assert "missing cookie" in capsys.readouterr().err
