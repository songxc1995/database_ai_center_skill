"""Domain implementations for the stable dba_api_client CLI.

Handlers receive the entrypoint's live globals as a runtime mapping. This keeps the
existing CLI and importlib-based callers monkeypatchable while relocating behavior
without duplicating transport, credential, pagination, or redaction rules.
"""
