"""Domain entities and rules: plain Python, with no database, HTTP, model SDK or FastAPI.

That is what lets them be tested without a network. There is no Pydantic either: a domain
object validates itself when built, so one that exists is valid.
"""
