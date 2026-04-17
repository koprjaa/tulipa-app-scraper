"""HTTP + session layer for the Tulipa Helios endpoint.

Wraps everything external into one class: `HeliosClient` owns the
`requests.Session`, the token cache, the RPC envelope, and retry logic.
Consumers (services) depend on this single object, not on module-level state.
"""
from __future__ import annotations

import gzip
import io
import json
import logging
import time
from datetime import datetime, timedelta

import requests
from requests.packages.urllib3.exceptions import InsecureRequestWarning  # type: ignore[attr-defined]

from tulipa_app_scraper.domain.errors import TulipaAPIError, TulipaSessionExpired
from tulipa_app_scraper.infrastructure.config import Settings


class HeliosClient:
    """Stateful client for the Tulipa B2B Helios JSON-RPC endpoint."""

    def __init__(self, settings: Settings, logger: logging.Logger | None = None) -> None:
        self.settings = settings
        self.logger = logger or logging.getLogger(__name__)

        self._http = requests.Session()
        self._http.headers.update(
            {
                "Content-Type": "application/json",
                "User-Agent": "Embarcadero RESTClient/1.0",
                "Accept-Encoding": "gzip, deflate, br",
            }
        )
        requests.packages.urllib3.disable_warnings(InsecureRequestWarning)  # type: ignore[attr-defined]

        self._session_token: str | None = None
        self._session_expires: datetime | None = None

    # --------------------------- Session management ---------------------------

    def get_token(self) -> str:
        """Return a valid session token, refreshing if necessary."""
        if self._token_valid():
            assert self._session_token is not None
            return self._session_token

        if loaded := self._load_token_from_disk():
            self._session_token, self._session_expires = loaded
            if self._token_valid():
                return self._session_token  # type: ignore[return-value]

        self.logger.info("Acquiring new session token via login")
        self._session_token = self._login()
        self._session_expires = datetime.now() + timedelta(
            minutes=self.settings.session_timeout_minutes
        )
        self._save_token_to_disk()
        return self._session_token

    def force_logout(self) -> None:
        """Wipe any saved session token on disk + in memory."""
        if self.settings.session_file.exists():
            try:
                data = json.loads(self.settings.session_file.read_text(encoding="utf-8"))
                if token := data.get("token"):
                    self.call(
                        {"_parameters": [token, "Logout", {"Version": "1.0"}, []]},
                        is_reset_call=True,
                    )
            except Exception as e:  # noqa: BLE001
                self.logger.debug(f"Logout RPC failed (non-fatal): {e}")
            self.settings.session_file.unlink(missing_ok=True)
        self._session_token = None
        self._session_expires = None
        self.logger.info("Session invalidated")

    def activate_database(self) -> None:
        """Many Helios actions require a `ChangeDatabase` call to set the context first."""
        token = self.get_token()
        self.call(
            {
                "_parameters": [
                    token,
                    "ChangeDatabase",
                    {"Version": "1.0", "DatabaseName": "Helios001"},
                    [],
                ]
            },
            is_reset_call=True,
        )

    # --------------------------- RPC primitives ---------------------------

    def call(self, payload: dict, is_reset_call: bool = False) -> dict | None:
        """Send a JSON-RPC payload. Returns the parsed JSON response or None on network failure."""
        try:
            if not is_reset_call:
                time.sleep(0.2)  # mild rate-limit courtesy
            if self.settings.debug:
                self.logger.debug(f"POST {self.settings.full_url}")
                self.logger.debug(f"Payload: {json.dumps(payload, ensure_ascii=False)[:500]}")
            response = self._http.post(
                self.settings.full_url,
                data=json.dumps(payload),
                verify=False,  # Tulipa uses self-signed cert
                timeout=self.settings.request_timeout,
            )
            response.raise_for_status()
            data = self._decode_response(response)
            self._log_server_error(data)
            return data
        except requests.exceptions.ConnectionError as e:
            self.logger.error(f"Connection error: {e}")
            return None
        except requests.exceptions.Timeout as e:
            self.logger.error(f"Timeout: {e}")
            return None
        except Exception as e:  # noqa: BLE001
            self.logger.error(f"Unexpected RPC error: {e}")
            if self.settings.debug:
                import traceback
                self.logger.debug(traceback.format_exc())
            return None

    def run_external_action(
        self,
        action_id: str,
        parameters: list | None = None,
    ) -> dict | None:
        """Run `RunExternalAction` with automatic one-shot token refresh on session errors."""
        payload = self._build_action_payload(action_id, parameters, self.get_token())
        response = self.call(payload)
        if self._is_token_error(response):
            self.logger.warning("Token error — refreshing and retrying once")
            self._session_token = None
            self._session_expires = None
            payload["_parameters"][0] = self.get_token()
            response = self.call(payload)
        return response

    def get_browse(self, browse_name: str | None = None) -> dict | None:
        """Run `GetBrowse` — either list all browse definitions, or fetch one by name."""
        token = self.get_token()
        params: dict = (
            {"Version": "1.0"}
            if browse_name is None
            else {"Version": "1.1", "BrowseName": browse_name}
        )
        return self.call({"_parameters": [token, "GetBrowse", params, []]})

    # --------------------------- Internals ---------------------------

    def _login(self) -> str:
        payload = {
            "_parameters": [
                "",
                "Login",
                {
                    "Version": "1.0",
                    "Username": self.settings.username,
                    "Password": self.settings.password,
                    "PluginSysName": "eServerTulipaMAT",
                    "DatabaseName": "Helios001",
                },
                [],
            ]
        }
        response = self.call(payload, is_reset_call=True)
        if not response or response["result"][0]["fields"]["IsError"]:
            msg = (
                response["result"][0]["fields"].get("ErrorMessage", "unknown error")
                if response
                else "no response"
            )
            raise TulipaAPIError(f"Login failed: {msg}")
        return response["result"][0]["fields"]["Result"]

    def _build_action_payload(
        self, action_id: str, parameters: list | None, token: str
    ) -> dict:
        return {
            "_parameters": [
                token,
                "RunExternalAction",
                {
                    "Version": "1.0",
                    "ActionID": action_id,
                    "SelectedRows": [],
                    "Parameters": parameters or [],
                },
                [],
            ]
        }

    def _token_valid(self) -> bool:
        return (
            self._session_token is not None
            and self._session_expires is not None
            and datetime.now() < self._session_expires
        )

    def _load_token_from_disk(self) -> tuple[str, datetime] | None:
        if not self.settings.session_file.exists():
            return None
        try:
            data = json.loads(self.settings.session_file.read_text(encoding="utf-8"))
            expires_at = datetime.fromisoformat(data["expires_at"])
            if datetime.now() > expires_at:
                self.settings.session_file.unlink(missing_ok=True)
                return None
            if data.get("username") != self.settings.username:
                self.settings.session_file.unlink(missing_ok=True)
                return None
            self.logger.info(
                f"Loaded valid session token (expires {expires_at:%H:%M:%S})"
            )
            return data["token"], expires_at
        except Exception as e:  # noqa: BLE001
            self.logger.warning(f"Failed to load session token: {e}")
            self.settings.session_file.unlink(missing_ok=True)
            return None

    def _save_token_to_disk(self) -> None:
        self.settings.session_file.parent.mkdir(parents=True, exist_ok=True)
        assert self._session_token is not None
        assert self._session_expires is not None
        data = {
            "token": self._session_token,
            "username": self.settings.username,
            "created_at": datetime.now().isoformat(),
            "expires_at": self._session_expires.isoformat(),
            "estimated_timeout": str(self._session_expires - datetime.now()),
        }
        self.settings.session_file.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        self.logger.info(f"Session token saved to {self.settings.session_file}")

    @staticmethod
    def _decode_response(response: requests.Response) -> dict:
        if "gzip" in response.headers.get("Content-Encoding", ""):
            try:
                with gzip.GzipFile(fileobj=io.BytesIO(response.content)) as f:
                    return json.loads(f.read().decode("utf-8"))
            except (gzip.BadGzipFile, EOFError):
                pass
        return response.json()

    def _log_server_error(self, response: dict | None) -> None:
        if not response:
            return
        try:
            fields = response["result"][0]["fields"]
            if fields.get("IsError"):
                msg = fields.get("ErrorMessage", "unknown")
                self.logger.warning(f"Server returned error: {msg}")
        except (KeyError, IndexError, TypeError):
            pass

    @staticmethod
    def _is_token_error(response: dict | None) -> bool:
        if not response:
            return False
        try:
            msg = (
                response.get("result", [{}])[0]
                .get("fields", {})
                .get("ErrorMessage", "")
                .lower()
            )
            return any(
                kw in msg for kw in ("session", "token", "login", "expired", "invalid")
            )
        except (KeyError, IndexError, AttributeError):
            return False

    @staticmethod
    def is_success(response: dict | None) -> bool:
        """True if the response is non-None and `IsError` is falsy."""
        if not response:
            return False
        try:
            return not response["result"][0]["fields"].get("IsError", True)
        except (KeyError, IndexError, TypeError):
            return False


# Re-export the TulipaSessionExpired so infrastructure users don't need to reach into domain
__all__ = ["HeliosClient", "TulipaSessionExpired"]
