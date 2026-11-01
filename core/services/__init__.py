"""Business rules, independent of HTTP and templates.

Views translate requests into service calls and service results into responses.
Services never touch `request`, `messages` or templates, so they can be tested
directly. Operations that change several related rows run in one transaction.
"""
