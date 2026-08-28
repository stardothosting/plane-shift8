"""GraphQL client for the Linear API.

Handles authentication, pagination, and rate-limit back-off.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)

LINEAR_API_URL = "https://api.linear.app/graphql"
DEFAULT_PAGE_SIZE = 50
# Linear allows 1 500 complexity points / minute.  We stay conservative.
RATE_LIMIT_SLEEP_SECONDS = 10
TRANSIENT_RETRY_SLEEP_SECONDS = 2
MAX_RETRIES = 20


class LinearClientError(Exception):
    """Raised when the Linear API returns an error."""

    def __init__(self, errors: list[dict], query: str | None = None):
        self.errors = errors
        self.query = query
        messages = "; ".join(e.get("message", str(e)) for e in errors)
        super().__init__(f"Linear API error: {messages}")


class LinearClient:
    """Thin wrapper around Linear's public GraphQL API."""

    def __init__(self, api_key: str, *, timeout: float = 60.0):
        if not api_key:
          raise ValueError("LINEAR_API_KEY must be provided")
        self._client = httpx.Client(
          base_url=LINEAR_API_URL,
          headers={
            "Authorization": api_key,
            "Content-Type": "application/json",
          },
          timeout=timeout,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> LinearClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Low-level
    # ------------------------------------------------------------------

    @staticmethod
    def _errors_from_response(resp: httpx.Response) -> list[dict[str, Any]]:
        try:
            body = resp.json()
        except ValueError:
            return []
        errors = body.get("errors")
        return errors if isinstance(errors, list) else []

    @staticmethod
    def _is_rate_limited(resp: httpx.Response, errors: list[dict[str, Any]]) -> bool:
        if resp.status_code == 429:
            return True
        for err in errors:
            ext = err.get("extensions") or {}
            if ext.get("code") == "RATELIMITED" or ext.get("type") == "ratelimited":
                return True
            if ext.get("statusCode") == 429:
                return True
        return False

    @staticmethod
    def _retry_after_wait(resp: httpx.Response) -> int | None:
        retry_after = resp.headers.get("Retry-After")
        if retry_after and retry_after.isdigit():
            return max(int(retry_after), RATE_LIMIT_SLEEP_SECONDS)
        return None

    @staticmethod
    def _reset_at_wait(errors: list[dict[str, Any]]) -> int | None:
        # Fall back to GraphQL extension metadata if available.
        for err in errors:
            ext = err.get("extensions") or {}
            result = ((ext.get("meta") or {}).get("rateLimitResult") or {})
            reset_at = result.get("resetAt")
            if not isinstance(reset_at, str):
                continue
            try:
                reset_dt = datetime.fromisoformat(reset_at.replace("Z", "+00:00"))
                remaining = int((reset_dt - datetime.now(timezone.utc)).total_seconds())
                if remaining > 0:
                    return max(remaining + 1, RATE_LIMIT_SLEEP_SECONDS)
            except ValueError:
                continue
        return None

    @classmethod
    def _rate_limit_sleep_seconds(cls, resp: httpx.Response, errors: list[dict[str, Any]]) -> int:
        retry_after_wait = cls._retry_after_wait(resp)
        if retry_after_wait is not None:
            return retry_after_wait

        reset_at_wait = cls._reset_at_wait(errors)
        if reset_at_wait is not None:
            return reset_at_wait

        return RATE_LIMIT_SLEEP_SECONDS

    def execute(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a GraphQL request with rate-limit and transient-error retries."""
        payload: dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = variables

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = self._client.post("", json=payload)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt == MAX_RETRIES:
                    raise LinearClientError(
                        [{"message": f"Network error after {MAX_RETRIES} attempts: {exc}"}],
                        query=query,
                    ) from exc
                logger.warning(
                    "Transient Linear API error (attempt %d/%d): %s; sleeping %ds",
                    attempt,
                    MAX_RETRIES,
                    exc,
                    TRANSIENT_RETRY_SLEEP_SECONDS,
                )
                time.sleep(TRANSIENT_RETRY_SLEEP_SECONDS)
                continue
            errors = self._errors_from_response(resp)

            if self._is_rate_limited(resp, errors):
                sleep_for = self._rate_limit_sleep_seconds(resp, errors)
                logger.warning(
                    "Rate-limited by Linear (attempt %d/%d), sleeping %ds",
                    attempt,
                    MAX_RETRIES,
                    sleep_for,
                )
                time.sleep(sleep_for)
                continue

            if resp.status_code >= 400:
                raise LinearClientError(
                    [{"message": f"HTTP {resp.status_code}: {resp.text[:500]}"}],
                    query=query,
                )

            body = resp.json()
            if "errors" in body:
                raise LinearClientError(body["errors"], query=query)
            return body["data"]

        raise LinearClientError(
            [{"message": "Rate limit exceeded after retries"}], query=query
        )

    # ------------------------------------------------------------------
    # Paginated helpers
    # ------------------------------------------------------------------

    def paginate(
        self,
        query: str,
        connection_path: str,
        variables: dict[str, Any] | None = None,
        *,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> list[dict[str, Any]]:
        """Auto-paginate a Relay-style connection and return all nodes.

        *connection_path* is a dot-separated path from the top-level ``data``
        object to the connection, e.g. ``"teams"`` or ``"team.issues"``.
        """
        variables = dict(variables or {})
        variables.setdefault("first", page_size)
        all_nodes: list[dict[str, Any]] = []

        while True:
            data = self.execute(query, variables)
            obj = data
            for key in connection_path.split("."):
                obj = obj[key]

            nodes = obj.get("nodes", [])
            all_nodes.extend(nodes)

            page_info = obj.get("pageInfo", {})
            if page_info.get("hasNextPage") and page_info.get("endCursor"):
                variables["after"] = page_info["endCursor"]
            else:
                break

        return all_nodes

    # ------------------------------------------------------------------
    # High-level data fetching
    # ------------------------------------------------------------------

    def fetch_organization(self) -> dict[str, Any]:
        query = """
        query {
          organization {
            id
            name
            urlKey
          }
        }
        """
        return self.execute(query)["organization"]

    def fetch_users(self) -> list[dict[str, Any]]:
        query = """
        query($first: Int!, $after: String) {
          users(first: $first, after: $after, includeArchived: true) {
            nodes {
              id
              name
              displayName
              email
              active
              admin
            }
            pageInfo { hasNextPage endCursor }
          }
        }
        """
        return self.paginate(query, "users")

    def fetch_teams(self) -> list[dict[str, Any]]:
        query = """
        query($first: Int!, $after: String) {
          teams(first: $first, after: $after) {
            nodes {
              id
              name
              key
              description
              states {
                nodes {
                  id
                  name
                  color
                  type
                  position
                }
              }
            }
            pageInfo { hasNextPage endCursor }
          }
        }
        """
        return self.paginate(query, "teams")

    def fetch_labels(self) -> list[dict[str, Any]]:
        query = """
        query($first: Int!, $after: String) {
          issueLabels(first: $first, after: $after) {
            nodes {
              id
              name
              color
              parent { id }
            }
            pageInfo { hasNextPage endCursor }
          }
        }
        """
        return self.paginate(query, "issueLabels")

    def fetch_issues_for_team(
        self, team_id: str, *, since: datetime | None = None
    ) -> list[dict[str, Any]]:
        """Fetch all issues for a team. Pass *since* for differential sync.

        When *since* is given, only issues with ``updatedAt >= since`` are
        returned, which cuts API request volume dramatically for incremental runs.
        Linear bumps ``updatedAt`` on an issue whenever a comment or state change
        is added, so changed sub-entities are not silently skipped.
        """
        if since is not None:
            query = """
            query($first: Int!, $after: String, $teamId: ID!, $since: DateTimeOrDuration!) {
              issues(
                first: $first,
                after: $after,
                filter: {
                  team: { id: { eq: $teamId } },
                  updatedAt: { gte: $since }
                },
                includeArchived: true
              ) {
                nodes {
                  id
                  identifier
                  title
                  description
                  descriptionState
                  priority
                  priorityLabel
                  state { id name }
                  assignee { id email }
                  labels { nodes { id } }
                  parent { id }
                  createdAt
                  updatedAt
                  startedAt
                  completedAt
                  canceledAt
                  dueDate
                  estimate
                  url
                }
                pageInfo { hasNextPage endCursor }
              }
            }
            """
            return self.paginate(
                query, "issues", {"teamId": team_id, "since": since.isoformat()}
            )

        query = """
        query($first: Int!, $after: String, $teamId: ID!) {
          issues(
            first: $first,
            after: $after,
            filter: { team: { id: { eq: $teamId } } },
            includeArchived: true
          ) {
            nodes {
              id
              identifier
              title
              description
              descriptionState
              priority
              priorityLabel
              state { id name }
              assignee { id email }
              labels { nodes { id } }
              parent { id }
              createdAt
              updatedAt
              startedAt
              completedAt
              canceledAt
              dueDate
              estimate
              url
            }
            pageInfo { hasNextPage endCursor }
          }
        }
        """
        return self.paginate(query, "issues", {"teamId": team_id})

    def fetch_comments_for_issue(self, issue_id: str) -> list[dict[str, Any]]:
        query = """
        query($first: Int!, $after: String, $issueId: ID!) {
          comments(
            first: $first,
            after: $after,
            filter: { issue: { id: { eq: $issueId } } }
          ) {
            nodes {
              id
              body
              createdAt
              updatedAt
              user { id email }
            }
            pageInfo { hasNextPage endCursor }
          }
        }
        """
        return self.paginate(query, "comments", {"issueId": issue_id})

    def fetch_attachments_for_issue(self, issue_id: str) -> list[dict[str, Any]]:
        query = """
        query($issueId: String!) {
          issue(id: $issueId) {
            attachments {
              nodes {
                id
                title
                subtitle
                url
                metadata
                source
                createdAt
              }
            }
          }
        }
        """
        data = self.execute(query, {"issueId": issue_id})
        return (data.get("issue") or {}).get("attachments", {}).get("nodes", [])

    def fetch_relations_for_issue(self, issue_id: str) -> list[dict[str, Any]]:
        query = """
        query($issueId: String!) {
          issue(id: $issueId) {
            relations {
              nodes {
                id
                type
                relatedIssue { id }
              }
            }
          }
        }
        """
        data = self.execute(query, {"issueId": issue_id})
        return (data.get("issue") or {}).get("relations", {}).get("nodes", [])
