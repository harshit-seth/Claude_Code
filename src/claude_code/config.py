"""Application configuration."""

import os
from dataclasses import dataclass
from typing import Final

from dotenv import find_dotenv, load_dotenv

MODEL: Final = "openai/gpt-oss-120b"
MODEL_CONTEXT_WINDOW: Final = 131_072


@dataclass(frozen=True)
class Config:
    """Runtime settings required by the application."""

    api_key: str


def load_config() -> Config:
    """Load settings from the environment and an optional .env file."""
    load_dotenv(find_dotenv(usecwd=True))
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise ValueError("GROQ_API_KEY must be set in the environment or .env file.")
    return Config(api_key=api_key)
