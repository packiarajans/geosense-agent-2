# app.py
# Purpose: Streamlit demo app - text question box, agent response, and the embedded 3D twin
import os
import sys
from pathlib import Path

import httpx
import streamlit as st
import streamlit.components.v1 as components

sys.path.insert(0, str(Path(__file__).resolve().parent))
from geoai_agent import ask_agent  # noqa: E402

MCP = os.getenv('MCP_SERVER_URL', 'http://localhost:8000')
TWIN = os.getenv('TWIN_URL', 'http://localhost:5173')

st.set_page_config(page_title='GeoSense Agent 2.0', layout='wide')
st.title('GeoSense Agent 2.0 - Conversational Geospatial Intelligence')


def first_location(tool_results):
    """Pick the first latitude/longitude the agent used, so the twin can fly there."""
    for r in tool_results:
        a = r.get('args', {}) or {}
        if 'latitude' in a and 'longitude' in a:
            return a['latitude'], a['longitude']
        if all(k in a for k in ('min_lat', 'max_lat', 'min_lon', 'max_lon')):
            return (a['min_lat'] + a['max_lat']) / 2, (a['min_lon'] + a['max_lon']) / 2
    return None


question = st.text_input('Ask a question about any location:', 'Is this area in Chennai suitable for a new clinic?')

if st.button('Ask GeoSense Agent'):
    with st.spinner('Agent is reasoning and calling tools...'):
        try:
            rec = ask_agent(question, thread_id='streamlit', return_details=True)
        except Exception as e:
            rec = None
            st.error(f'Agent error: {type(e).__name__}: {e}')
    if rec:
        st.success(rec['answer'])
        loc = first_location(rec['tool_results'])
        if loc:
            try:
                httpx.post(f'{MCP}/agent/latest_result', json={'latitude': loc[0], 'longitude': loc[1]}, timeout=10)
                st.caption(f'3D twin is flying to {loc[0]:.4f}, {loc[1]:.4f}')
            except Exception as e:
                st.warning(f'Could not tell the twin where to fly: {e}')
        with st.expander('Tools the agent called'):
            st.json(rec['tool_results'])

st.subheader('3D Digital Twin')
components.iframe(TWIN, height=600, scrolling=True)
