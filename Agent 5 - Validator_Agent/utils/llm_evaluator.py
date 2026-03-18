import os
import json
import requests
from dotenv import load_dotenv

load_dotenv()

def evaluate_narrative(narrative_text: str) -> dict:
    """
    Evaluate SAR narrative quality using ZhiPu GLM-5 API.
    Returns a dict with keys: score, missing_elements, comments.
    """
    api_key = os.getenv("DEFAULT_LLM_API_KEY")
    base_url = os.getenv("DEFAULT_LLM_BASE_URL")
    model = os.getenv("DEFAULT_LLM_MODEL_NAME", "glm-5")

    if not api_key:
        raise ValueError("LLM API key not found in environment")

    prompt = f"""You are a BSA/AML compliance expert. Assess the following SAR narrative according to FinCEN guidelines.
The narrative should clearly include:
- Who (subject identification)
- What (description of suspicious transactions, including amounts and instruments)
- When (date range of activity)
- Where (locations, including branch and foreign jurisdictions)
- Why (reasons for suspicion, e.g., structuring, unusual patterns)
- How (method of operation, e.g., funds flow, account usage)

Additionally, check for:
- Prohibited phrases like "see attached"
- Clear description of funds flow (origination and destination)

Provide a JSON output with:
- "score": an integer from 0 to 100
- "missing_elements": a list of missing elements (as strings)
- "comments": a brief comment on quality

Narrative:
{narrative_text}
"""

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"}
    }

    try:
        response = requests.post(f"{base_url}/chat/completions", json=payload, headers=headers, timeout=30)
        response.raise_for_status()
        result = response.json()
        content = result["choices"][0]["message"]["content"]
        return json.loads(content)
    except Exception as e:
        # Fallback in case of API failure
        return {
            "score": 50,
            "missing_elements": ["LLM evaluation failed"],
            "comments": f"API call error: {str(e)}"
        }