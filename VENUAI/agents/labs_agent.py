"""DRDO VenuAI — Labs Specialist Agent (DRDO labs, clusters, locations, org structure)"""
from VENUAI.agents.base_agent import BaseSpecialistAgent

class LabsAgent(BaseSpecialistAgent):
    """
    Specialist for DRDO laboratory information: cluster structure,
    lab mandates, locations, key projects, and organisational hierarchy.
    """
    name = "LabsAgent"
    domain = "labs"
    system_prompt = (
        "You are VenuAI's DRDO Laboratories Specialist. "
        "You have deep knowledge of all DRDO laboratories, their cluster groupings, "
        "geographic locations, organisational mandates, and key research areas. "
        "Present lab information in a clear table: | Lab Code | Name | Location | Mandate |. "
        "Bold all lab codes (ARDE, DRDL, LRDE, etc.) and cluster names. "
        "If a lab is not in the context, acknowledge that and do not invent details."
    )
    metadata_filter = None
