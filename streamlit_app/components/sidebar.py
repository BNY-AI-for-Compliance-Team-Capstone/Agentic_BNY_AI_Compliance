"""Sidebar navigation and health widget."""

from __future__ import annotations

import time

import streamlit as st

from streamlit_app.utils.api_client import APIClient, APIClientError


def render_sidebar(api_client: APIClient) -> None:
    with st.sidebar:
        st.markdown("## Navigation")
        # Paths must be relative to the Streamlit main script directory.
        st.page_link("app.py", label="Home")
        st.page_link("pages/1_Dashboard.py", label="Dashboard")
        st.page_link("pages/2_Submit_Case.py", label="Submit Case")
        st.page_link("pages/3_Case_Management.py", label="Case Management")
        st.page_link("pages/6_Settings.py", label="Settings")

        st.markdown("---")
        st.markdown("### Backend Health")
        health = st.session_state.get("_sidebar_health_cache")
        last_check = float(st.session_state.get("_sidebar_health_checked_at", 0))
        if (not isinstance(health, dict)) or (time.time() - last_check > 15):
            try:
                health = api_client.health_check()
                st.session_state["_sidebar_health_cache"] = health
                st.session_state["_sidebar_health_checked_at"] = time.time()
            except Exception:
                # Keep prior cached health when possible to avoid transient flicker.
                if not isinstance(health, dict):
                    health = {"status": "unavailable", "services": {}}
                    st.session_state["_sidebar_health_cache"] = health
                st.session_state["_sidebar_health_checked_at"] = time.time()

        services = health.get("services", {}) if isinstance(health, dict) else {}
        all_up = all(services.get(svc) is True for svc in ("database", "weaviate", "redis")) if isinstance(services, dict) else False

        if isinstance(health, dict) and health.get("status") == "healthy" and all_up:
            st.success("Connected")
        elif isinstance(health, dict):
            st.warning("Backend slow or unavailable")
        else:
            st.error("Backend unavailable")

        for svc in ("database", "weaviate", "redis"):
            if services.get(svc) is True:
                st.caption(f"{svc}: up")
            elif services.get(svc) is False:
                st.caption(f"{svc}: down")
            elif services.get(svc) == "skipped":
                st.caption(f"{svc}: skipped")

        st.markdown("---")
        st.caption(f"Tracked jobs: {len(st.session_state.get('tracked_jobs', []))}")
