"""Drive upload transport: HTTP 308 belongs to the resumable upload protocol."""
import json
import os
from typing import Any


def resumable_http(http):
    # httplib2 otherwise follows Drive's 308 Resume Incomplete response as a
    # redirect and raises RedirectMissingLocation on every multi-chunk upload.
    http.redirect_codes = http.redirect_codes - {308}
    return http


def drive_client(scope="https://www.googleapis.com/auth/drive", timeout=60):
    import httplib2  # type: ignore[reportMissingImports]
    import google_auth_httplib2  # type: ignore[reportMissingImports]
    from google.oauth2 import service_account  # type: ignore[reportMissingImports]
    from googleapiclient.discovery import build  # type: ignore[reportMissingImports]
    info = json.loads(os.environ["MCD2_GDRIVE_SA_JSON"])
    credentials = service_account.Credentials.from_service_account_info(info, scopes=[scope])
    transport = resumable_http(httplib2.Http(timeout=timeout))
    http = google_auth_httplib2.AuthorizedHttp(credentials, http=transport)
    return build("drive", "v3", http=http, cache_discovery=False)


def safe_failure(exc):
    """Report a useful category/status without URLs, credentials or raw bodies."""
    import re
    result: dict[str, Any] = {"type": type(exc).__name__}
    status = getattr(getattr(exc, "resp", None), "status", None)
    if isinstance(status, int):
        result["http_status"] = status
    try:
        body = json.loads(getattr(exc, "content", b"{}"))
        reasons = [e.get("reason", "") for e in body.get("error", {}).get("errors", [])]
        result["reasons"] = [r for r in reasons if isinstance(r, str) and re.fullmatch(r"[A-Za-z_]{1,64}", r)]
    except (ValueError, TypeError, AttributeError):
        pass
    return result
