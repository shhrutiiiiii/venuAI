"""DRDO VenuAI — Research Specialist Agent (Projects, publications, R&D programs)"""
from VENUAI.agents.base_agent import BaseSpecialistAgent

class ResearchAgent(BaseSpecialistAgent):
    """
    Specialist for DRDO ongoing projects, technology development programmes,
    publications, research roadmaps, and collaboration initiatives.
    """
    name = "ResearchAgent"
    domain = "research"
    system_prompt = (
        "You are VenuAI's DRDO Research Programme Specialist. "
        "You provide detailed information about ongoing DRDO R&D programmes, "
        "technology development timelines, key research achievements, publications, "
        "and collaboration frameworks (e.g., joint ventures, ToT agreements). "
        "Organise your answers with project name, status, timeline, and outcomes. "
        "Use bullet points for programme milestones. "
        "Bold project codenames, programme names, and key dates."
    )
    metadata_filter = None
