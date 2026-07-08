from decimal import Decimal

import pytest
from pydantic import ValidationError

from agent.config import load_config


def _write(tmp_path, text):
    p = tmp_path / "config.yaml"
    p.write_text(text)
    return str(p)


def test_loads_the_example_config(tmp_path):
    path = _write(
        tmp_path,
        """
        strategy:
          type: GRID
          ticker: BTCUSD
          balance: 50000
          prompt: |
            Trade BTC/USD conservatively.
            Require strong confirmation before entering.
            Never use leverage.
        logging:
          level: INFO
          format: json
        """,
    )
    config = load_config(path)
    assert config.strategy.type == "GRID"
    assert config.strategy.ticker == "BTCUSD"
    assert config.strategy.balance == Decimal("50000")
    assert "Never use leverage." in config.strategy.prompt
    assert config.logging.level == "INFO"
    assert config.logging.format == "json"


def test_logging_defaults_when_omitted(tmp_path):
    path = _write(
        tmp_path,
        """
        strategy:
          type: DCA
          ticker: ETHUSD
          balance: 1000
          prompt: "Buy the dip."
        """,
    )
    config = load_config(path)
    assert config.logging.level == "INFO"
    assert config.logging.format == "json"


def test_rejects_unknown_strategy_type(tmp_path):
    path = _write(
        tmp_path,
        """
        strategy:
          type: MARTINGALE
          ticker: BTCUSD
          balance: 50000
          prompt: "Go wild."
        """,
    )
    with pytest.raises(ValidationError):
        load_config(path)


def test_rejects_missing_prompt(tmp_path):
    path = _write(
        tmp_path,
        """
        strategy:
          type: TWAP
          ticker: BTCUSD
          balance: 50000
        """,
    )
    with pytest.raises(ValidationError):
        load_config(path)
