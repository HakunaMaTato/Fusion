import pytest
from pydantic import ValidationError

from app.nansen.models import TokenScreenerResponse


def test_missing_required_field_fails_loudly() -> None:
    payload = {
        "data": [{"chain": "solana", "token_symbol": "PUMP"}],  # missing token_address
        "pagination": {"page": 1, "per_page": 10, "is_last_page": True},
    }

    with pytest.raises(ValidationError):
        TokenScreenerResponse.model_validate(payload)


def test_unknown_fields_are_allowed() -> None:
    payload = {
        "data": [
            {
                "chain": "solana",
                "token_address": "abc",
                "token_symbol": "PUMP",
                "a_brand_new_field_nansen_added_later": 42,
            }
        ],
        "pagination": {"page": 1, "per_page": 10, "is_last_page": True},
    }

    parsed = TokenScreenerResponse.model_validate(payload)

    assert parsed.data[0].token_symbol == "PUMP"
