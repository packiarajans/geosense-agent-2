# geoai_agent.py
# Purpose: LangGraph GeoAI agent. Takes a plain-English question, decides which GeoSense tools to call (with real
# arguments extracted from the question), calls them on the MCP server, and writes an evidence-based answer.
#
# Graph:  understand_query --(needs tools)--> execute_tools --> synthesize_response --> END
#                          \--(needs clarification / no tool needed)---------------^
# Differences from the manual's sketch (all deliberate):
#   * tool ARGUMENTS come from the question (the manual always queries one fixed Chennai point);
#   * the graph is built ONCE, so MemorySaver really remembers earlier turns of a thread;
#   * out-of-coverage / unavailable tools are reported honestly instead of being papered over;
#   * every run is saved to outputs/agent_logs/ (useful for the demo and debugging).
import os, sys, json, time
from pathlib import Path
from typing import TypedDict, Annotated, Literal, Optional
import operator

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

try:                                   # corporate / proxy networks: use the Windows certificate store
    import truststore; truststore.inject_into_ssl()
except Exception:
    pass

import httpx
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

load_dotenv(ROOT / '.env')
MCP_SERVER_URL = os.getenv('MCP_SERVER_URL', 'http://localhost:8000')
MODEL = os.getenv('AGENT_MODEL', 'claude-sonnet-4-6')
LOG_DIR = ROOT / 'outputs' / 'agent_logs'

TOOL_GUIDE = """
Tools (all take WGS84 decimal degrees):
- score_location(latitude, longitude): ML site-suitability score (0-10 opportunity/risk) + SHAP reasons. CHENNAI ONLY.
- get_climate_risk(latitude, longitude): elevation (m) + flood-zone class (high/medium/low). Chennai and Manhattan NYC.
- get_demographics(latitude, longitude): nearest hospital (name, km) from OSM. Population density is NOT available.
- find_similar_sites(latitude, longitude, top_k): labelled Chennai candidate sites with a similar profile. CHENNAI ONLY.
- lidar_profile(min_lat, min_lon, max_lat, max_lon): building height, canopy %, building cover %, ground slope from LiDAR. Manhattan/NYC study areas only (e.g. Upper East Side around 40.772,-73.962 to 40.778,-73.955).
- streetview_scene(latitude, longitude): street-view scene composition + walkability score (slow; only if asked about streets/walkability).
- classify_imagery(latitude, longitude): satellite land cover. Currently UNAVAILABLE - call it only if the user explicitly asks for land cover.
Coverage: Chennai approx lat 12.97-13.24, lon 80.13-80.33; Manhattan approx lat 40.68-40.88, lon -74.05 to -73.91.
"""

class ToolCall(BaseModel):
    tool: Literal['score_location', 'classify_imagery', 'lidar_profile', 'streetview_scene',
                  'get_climate_risk', 'get_demographics', 'find_similar_sites']
    args: dict = Field(description='Arguments exactly as listed in the tool guide, numbers only.')
    label: str = Field(default='', description='Short name of the place this call is about, e.g. "Marina Beach".')

class Plan(BaseModel):
    calls: list[ToolCall] = Field(default_factory=list)
    clarification: Optional[str] = Field(default=None, description='Ask this if the question has no usable location or is too vague to act on; leave empty otherwise.')
    approximate_coordinates: bool = Field(default=False, description='True if you estimated coordinates for a named place instead of using coordinates given by the user.')

class AgentState(TypedDict):
    question: str
    history: Annotated[list, operator.add]     # [{"q":..., "a":...}] kept per thread by MemorySaver
    plan: dict
    tool_results: list
    final_answer: str

# ------------------------------------------------------------------ LLM calls (kept separate so they can be stubbed in tests)
_llm = None
def get_llm():
    global _llm
    if _llm is None:
        key = os.getenv('ANTHROPIC_API_KEY')
        if not key:
            raise SystemExit('ANTHROPIC_API_KEY is not set. Add it to F:\\GeoSense_Agent\\.env (line: ANTHROPIC_API_KEY=sk-ant-...).')
        from langchain_anthropic import ChatAnthropic
        kw = {}
        ws = os.getenv('ANTHROPIC_WORKSPACE_ID', '').strip()
        if ws:   # needed when the API key is not scoped to one workspace
            kw['default_headers'] = {'anthropic-workspace-id': ws}
        _llm = ChatAnthropic(model=MODEL, api_key=key, max_tokens=1500, **kw)
    return _llm

def _history_text(history):
    return '\n'.join(f"User: {h['q']}\nAssistant: {h['a']}" for h in history[-4:]) or '(none)'

def plan_with_llm(question, history) -> Plan:
    prompt = (f"You plan tool calls for a geospatial assistant.\n{TOOL_GUIDE}\n"
              f"Earlier conversation:\n{_history_text(history)}\n\nNew question: {question}\n\n"
              "Choose the minimum set of tools that answers the question. Use coordinates given by the user; for a well-known "
              "named place inside the coverage areas you may estimate coordinates (set approximate_coordinates=true). "
              "If there is no usable location, put a short question in `clarification` and no calls. "
              "If the user asks for something no tool can provide (e.g. temperature, drought, population density), still call the closest "
              "tool if useful and let the answer say what is missing.")
    return get_llm().with_structured_output(Plan).invoke(prompt)

def write_with_llm(question, history, results, plan) -> str:
    prompt = ("You are GeoSense, a geospatial analyst. Answer the user's question using ONLY the tool results below.\n"
              "Rules: quote the key numbers; if a tool returned an error, null, or said unavailable / out of coverage, say so plainly "
              "and do not guess; note that flood classes are DEM-elevation heuristics, not hydraulic models; if coordinates were estimated, "
              "say they are approximate; keep it concise (under ~200 words), no markdown tables.\n\n"
              f"Earlier conversation:\n{_history_text(history)}\n\nQuestion: {question}\n"
              f"Approximate coordinates used: {plan.get('approximate_coordinates')}\n"
              f"Tool results (JSON):\n{json.dumps(results, default=str)[:12000]}")
    return get_llm().invoke(prompt).content

# ------------------------------------------------------------------ graph nodes
def understand_query(state: AgentState) -> dict:
    plan = plan_with_llm(state['question'], state.get('history', []))
    return {'plan': plan.model_dump()}

def route_after_plan(state: AgentState) -> str:
    p = state['plan']
    return 'execute_tools' if p.get('calls') and not p.get('clarification') else 'synthesize_response'

def execute_tools(state: AgentState) -> dict:
    results = []
    with httpx.Client(timeout=120) as client:
        for c in state['plan']['calls']:
            t0 = time.time()
            try:
                r = client.post(f"{MCP_SERVER_URL}/tools/{c['tool']}", json=c['args'])
                body = r.json() if r.headers.get('content-type', '').startswith('application/json') else {'error': r.text[:300]}
                if r.status_code >= 400:
                    body = {'error': f'HTTP {r.status_code}', 'detail': body}
            except httpx.ConnectError:
                body = {'error': f'Cannot reach the MCP server at {MCP_SERVER_URL}. Start run_mcp_server.bat first.'}
            except Exception as e:
                body = {'error': f'{type(e).__name__}: {e}'}
            results.append({'tool': c['tool'], 'args': c['args'], 'label': c.get('label', ''),
                            'seconds': round(time.time() - t0, 1), 'result': body})
    return {'tool_results': results}

def synthesize_response(state: AgentState) -> dict:
    plan = state['plan']
    if plan.get('clarification') and not plan.get('calls'):
        answer = plan['clarification']
    else:
        answer = write_with_llm(state['question'], state.get('history', []), state.get('tool_results', []), plan)
    return {'final_answer': answer, 'history': [{'q': state['question'], 'a': answer}]}

_app = None
def build_agent_graph():
    global _app
    if _app is None:
        g = StateGraph(AgentState)
        g.add_node('understand_query', understand_query)
        g.add_node('execute_tools', execute_tools)
        g.add_node('synthesize_response', synthesize_response)
        g.set_entry_point('understand_query')
        g.add_conditional_edges('understand_query', route_after_plan,
                                {'execute_tools': 'execute_tools', 'synthesize_response': 'synthesize_response'})
        g.add_edge('execute_tools', 'synthesize_response')
        g.add_edge('synthesize_response', END)
        _app = g.compile(checkpointer=MemorySaver())
    return _app

def ask_agent(question: str, thread_id: str = 'default-session', return_details: bool = False):
    app = build_agent_graph()
    cfg = {'configurable': {'thread_id': thread_id}}
    result = app.invoke({'question': question, 'tool_results': [], 'plan': {}, 'final_answer': ''}, cfg)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    rec = {'time': time.strftime('%Y-%m-%d %H:%M:%S'), 'thread': thread_id, 'model': MODEL, 'question': question,
           'plan': result['plan'], 'tool_results': result['tool_results'], 'answer': result['final_answer']}
    with open(LOG_DIR / f"agent_{time.strftime('%Y%m%d')}.jsonl", 'a', encoding='utf-8') as f:
        f.write(json.dumps(rec, default=str) + '\n')
    return rec if return_details else result['final_answer']

if __name__ == '__main__':
    if len(sys.argv) > 1:
        print(ask_agent(' '.join(sys.argv[1:])))
    else:
        print(f'GeoSense agent ({MODEL}) - type a question, blank line to quit. MCP server: {MCP_SERVER_URL}')
        while True:
            q = input('\nYou: ').strip()
            if not q:
                break
            print('\nGeoSense:', ask_agent(q, thread_id='cli'))

