"""Runtime settings for Streamlit frontend."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _get_api_base_url() -> str:
    # 1. Environment variable (local dev / Docker)
    if url := os.getenv("API_BASE_URL"):
        return url
    # 2. Streamlit Cloud secrets
    try:
        import streamlit as st
        if "API_BASE_URL" in st.secrets:
            return st.secrets["API_BASE_URL"]
    except Exception:
        pass
    return "http://localhost:8001"


@dataclass
class AppSettings:
    api_base_url: str = field(default_factory=_get_api_base_url)
    request_timeout_seconds: int = int(os.getenv("STREAMLIT_TIMEOUT", "30"))
    request_retries: int = int(os.getenv("STREAMLIT_RETRIES", "2"))
    page_title: str = "BNY Mellon Compliance AI"
    page_icon: str = "streamlit_app/assets/favicon.ico"
    layout: str = "wide"


settings = AppSettings()
