"""Paper search: the PaperSearchProvider seam and its implementations.

One interface over arXiv, OpenAlex, Europe PMC and Google Scholar. Each has its
own query syntax, rate limits and metadata shape, and none of that is allowed
above this package.
"""
