from decimal import Decimal
from typing import Literal

import yaml
from pydantic import BaseModel


class Strategy(BaseModel):
    type: Literal["DCA", "GRID", "TWAP"]
    ticker: str
    balance: Decimal
    prompt: str


class Logging(BaseModel):
    level: str = "INFO"
    format: Literal["text", "json"] = "json"


class Config(BaseModel):
    strategy: Strategy
    logging: Logging = Logging()


def load_config(path: str) -> Config:
    with open(path) as f:
        raw = yaml.safe_load(f)
    return Config.model_validate(raw)
