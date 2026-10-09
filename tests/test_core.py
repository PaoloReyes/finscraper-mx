import json
from decimal import Decimal

import pytest

import finscraper_mx.cli as cli
from finscraper_mx import PROVIDERS, AccountKind, AuthRequired, Balance, Provider, get_provider


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


def test_registry_resolves_every_provider():
    for id in PROVIDERS:
        assert get_provider(id).id == id
    with pytest.raises(KeyError, match="unknown institution"):
        get_provider("nope")


def test_cli_balances(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "get_provider", lambda id: Fake)
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"token": "x"}))
    assert cli.main(["balances", "fake", "--auth", str(auth)]) == 0
    assert json.loads(capsys.readouterr().out)[0]["account"] == "Cuenta *0001"


def test_cli_reports_missing_auth(tmp_path, capsys):
    auth = tmp_path / "auth.json"
    auth.write_text("{}")
    assert cli.main(["balances", "bitso", "--auth", str(auth)]) == 1
    assert "missing api_key" in capsys.readouterr().err
