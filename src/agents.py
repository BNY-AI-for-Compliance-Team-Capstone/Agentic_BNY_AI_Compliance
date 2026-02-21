from crewai import Agent
from langchain_google_genai import ChatGoogleGenerativeAI
import os

def get_hongyi_validator_agent() -> Agent:
    llm = ChatGoogleGenerativeAI(
        model="gemini-1.5-pro",
        temperature=0.0,  # Strictly deterministic LLM processing
        google_api_key=os.getenv("GEMINI_API_KEY")
    )

    return Agent(
        role="Agent 5: Validator - Hongyi",
        goal="Ensure absolute quality control for generated SAR cases against Legal Requirements and data fidelity.",
        backstory=(
            "You are an elite Quality Gate Validator for an AI for Compliance pipeline. "
            "You rigorously check data accuracy (ensuring JSON values match the narrative text), "
            "evaluate narrative clarity, and cross-reference Legal Requirements from the Knowledge Base. "
            "You synthesize deterministic rule outputs with your semantic analysis to make a final routing decision."
        ),
        verbose=True,
        allow_delegation=False,
        llm=llm
    )