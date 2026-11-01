import logging

import requests
from django.conf import settings


logger = logging.getLogger(__name__)


class IntegrationError(Exception):
    pass


def post_json(
    url,
    payload,
    *,
    timeout=10,
    headers=None,
):
    try:
        response = requests.post(
            url,
            json=payload,
            headers=headers or {},
            timeout=timeout,
        )

        response.raise_for_status()

        return response.json()

    except requests.RequestException as exc:
        logger.exception(
            "External integration failed: %s",
            type(exc).__name__,
        )

        raise IntegrationError(
            "External service request failed."
        ) from exc