import json
import urllib.error
import urllib.request

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

BASE_URL = "https://7vjdhmpxwb.execute-api.ap-southeast-2.amazonaws.com/dev"
REGION = "ap-southeast-2"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def submit_command(path, payload):
    if path not in ("/orders", "/cancellations"):
        raise ValueError("Unsupported API path")

    credentials = boto3.Session().get_credentials()
    if credentials is None:
        raise RuntimeError("No AWS credentials found in WSL")

    url = BASE_URL + path
    body = json.dumps(payload).encode("utf-8")

    signed = AWSRequest(
        method="POST",
        url=url,
        data=body,
        headers={"Content-Type": "application/json"},
    )
    SigV4Auth(
        credentials.get_frozen_credentials(),
        "execute-api",
        REGION,
    ).add_auth(signed)

    request = urllib.request.Request(
        url,
        data=body,
        headers=dict(signed.headers.items()),
        method="POST",
    )

    opener = urllib.request.build_opener(NoRedirect())

    try:
        with opener.open(request, timeout=30) as response:
            return response.status, json.loads(response.read().decode())
    except urllib.error.HTTPError as error:
        return error.code, {
            "error": error.read().decode("utf-8", errors="replace")
        }
    except (urllib.error.URLError, TimeoutError) as error:
        raise RuntimeError(
            "Submission not confirmed. Check the order or cancellation "
            "receipt before retrying the identical command."
        ) from error
