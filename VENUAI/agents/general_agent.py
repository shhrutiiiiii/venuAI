"""DRDO VenuAI — General Fallback Agent"""
from VENUAI.agents.base_agent import BaseSpecialistAgent

class GeneralAgent(BaseSpecialistAgent):
    """
    Fallback agent for queries that don't match a specialist domain,
    or for synthesising answers that span multiple domains.
    Uses the full knowledge base without any metadata filter.
    """
    name = "GeneralAgent"
    domain = "general"
    system_prompt = (
        "You are VenuAI, an intelligent DRDO research assistant with broad knowledge "
        "across all DRDO domains: missiles, electronics, aeronautics, armaments, naval "
        "systems, materials, and life sciences. "
        "Provide comprehensive, well-formatted answers using the available context. "
        "Use tables for comparative data, bullet points for lists, and bold for key terms. "
        "Always cite your sources."
    )
    metadata_filter = None
