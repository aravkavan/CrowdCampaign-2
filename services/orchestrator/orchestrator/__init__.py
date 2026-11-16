"""CrowdCampaign orchestrator (Assignment 2).

Plain code, no agent framework. It starts a run, splits one budget between two workers, calls them
over HTTP, checks everything they send back, writes the report, and is the only part that keeps
anything between runs (through the CrowdCampaign API).

    python -m orchestrator run        research every campaign in config.yaml, verify the claims, save the run
    python -m orchestrator check      are both workers and the app awake? (also wakes them up)
    python -m orchestrator status     what the app remembers
    python -m orchestrator reset      forget every saved run (asks first)
    python -m orchestrator stats      round trips, time and tokens from the trace logs
"""
