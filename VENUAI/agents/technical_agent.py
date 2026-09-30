"""DRDO VenuAI — Technical Specialist Agent (Weapons, specs, materials, propulsion, electronics)"""
from VENUAI.agents.base_agent import BaseSpecialistAgent

class TechnicalAgent(BaseSpecialistAgent):
    """
    Specialist for weapons specifications, engineering parameters,
    materials science, propulsion systems, and electronics.
    """
    name = "TechnicalAgent"
    domain = "technical"
    system_prompt = (
        "You are VenuAI's Technical Specialist — an expert in DRDO weapons systems, "
        "engineering specifications, materials science, propulsion, and electronics. "
        "Always present data in Markdown tables with columns for Parameter and Value. "
        "Bold all measurements, lab codes, and weapon names. "
        "Cite the source document and page number at the end of every answer. "
        "If technical data is not in the context, say so explicitly — do not guess."
    )
    metadata_filter = None  # no filter — technical docs span all labs
