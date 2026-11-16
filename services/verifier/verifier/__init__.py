"""CrowdCampaign verifier (Assignment 2, worker 2 of 2).

POST /verify {claims: [{claim_id, text, source_url}]} -> one verdict per claim:
supported (with an excerpt from the page), unsupported, or unreachable.

The verifier fetches each URL itself. It never receives the researcher's reasoning, summaries,
quotes or fetched pages, only the claim text and the URL it cites.

    python -m verifier              start the service (default port 8002; hosts like Render set $PORT)
"""
__version__ = "2.0.0"
