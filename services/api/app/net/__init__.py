"""Outbound HTTP: the one way any part of the system talks to another server.

Paper sources, PDF hosts and the model server are all reached through an
`HttpFetcher`, so the rules about where requests may go are enforced in one
place. Nothing outside this package imports an HTTP client.
"""
