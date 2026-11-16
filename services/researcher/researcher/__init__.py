"""CrowdCampaign researcher (Assignment 2, worker 1 of 2).

The Assignment 1B market-intel agent, wrapped as a small HTTP service. It answers
POST /research {topic, k} with the k most important recent developments, each with claims that cite one
source URL. It remembers nothing between requests: remembering is the orchestrator's job.

    python -m researcher            start the service (default port 8001)
"""
__version__ = "2.0.0"
