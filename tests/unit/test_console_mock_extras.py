"""console_api/mock_extras.py: additive, put-if-absent mock attributes."""
from __future__ import annotations

from opsresolve.common.config import settings
from opsresolve.console_api.mock_extras import MOCK_EXTRAS, PK_PREFIX, missing_attributes


def test_missing_attributes_returns_only_absent_keys():
    existing = {"pk": "ASSETS#X", "sk": "META", "installDate": "2022-03-14"}
    wanted = {"installDate": "1999-01-01", "displayName": "X"}
    assert missing_attributes(existing, wanted) == {"displayName": "X"}


def test_missing_attributes_never_returns_an_existing_key_even_if_value_differs():
    existing = {"role": "Owner", "name": None}
    assert missing_attributes(existing, {"role": "Account holder", "name": "N"}) == {}


def test_missing_attributes_never_creates_an_item():
    assert missing_attributes(None, {"displayName": "X"}) == {}


def test_extras_target_only_the_four_join_tables():
    assert set(MOCK_EXTRAS) == {settings.mock_contacts_table, settings.mock_sites_table,
                                settings.mock_assets_table, settings.mock_contracts_table}


def test_every_pk_uses_its_table_prefix():
    for table, items in MOCK_EXTRAS.items():
        for pk in items:
            assert pk.startswith(PK_PREFIX[table]), f"{table} {pk}"


def test_extras_never_touch_key_attributes():
    for items in MOCK_EXTRAS.values():
        for attrs in items.values():
            assert not {"pk", "sk"} & set(attrs)


def test_extras_cover_the_14_items():
    assert sum(len(v) for v in MOCK_EXTRAS.values()) == 14
