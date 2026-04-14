# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Tests to validate that stripped features (cycles, modules, pages, stickies,
analytics) are no longer reachable via the internal API.
"""

import pytest
from rest_framework import status
from uuid import uuid4


@pytest.mark.contract
class TestStrippedFeaturesInternalAPI:
    """Verify that stripped feature endpoints return 404 on the internal API."""

    FAKE_PROJECT_ID = str(uuid4())

    @pytest.mark.django_db
    def test_cycles_list_returns_404(self, session_client, workspace):
        url = f"/api/workspaces/{workspace.slug}/projects/{self.FAKE_PROJECT_ID}/cycles/"
        response = session_client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.django_db
    def test_cycle_detail_returns_404(self, session_client, workspace):
        url = f"/api/workspaces/{workspace.slug}/projects/{self.FAKE_PROJECT_ID}/cycles/{uuid4()}/"
        response = session_client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.django_db
    def test_modules_list_returns_404(self, session_client, workspace):
        url = f"/api/workspaces/{workspace.slug}/projects/{self.FAKE_PROJECT_ID}/modules/"
        response = session_client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.django_db
    def test_module_detail_returns_404(self, session_client, workspace):
        url = f"/api/workspaces/{workspace.slug}/projects/{self.FAKE_PROJECT_ID}/modules/{uuid4()}/"
        response = session_client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.django_db
    def test_pages_list_returns_404(self, session_client, workspace):
        url = f"/api/workspaces/{workspace.slug}/projects/{self.FAKE_PROJECT_ID}/pages/"
        response = session_client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.django_db
    def test_page_detail_returns_404(self, session_client, workspace):
        url = f"/api/workspaces/{workspace.slug}/projects/{self.FAKE_PROJECT_ID}/pages/{uuid4()}/"
        response = session_client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.django_db
    def test_stickies_list_returns_404(self, session_client, workspace):
        url = f"/api/workspaces/{workspace.slug}/stickies/"
        response = session_client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.django_db
    def test_sticky_detail_returns_404(self, session_client, workspace):
        url = f"/api/workspaces/{workspace.slug}/stickies/{uuid4()}/"
        response = session_client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.django_db
    def test_analytics_returns_404(self, session_client, workspace):
        url = f"/api/workspaces/{workspace.slug}/analytics/"
        response = session_client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.django_db
    def test_analytic_view_returns_404(self, session_client, workspace):
        url = f"/api/workspaces/{workspace.slug}/analytic-view/"
        response = session_client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.contract
class TestStrippedFeaturesExternalAPI:
    """Verify that stripped feature endpoints return 404 on the external API (v1)."""

    FAKE_PROJECT_ID = str(uuid4())

    @pytest.mark.django_db
    def test_cycles_v1_returns_404(self, api_key_client, workspace):
        url = f"/api/v1/workspaces/{workspace.slug}/projects/{self.FAKE_PROJECT_ID}/cycles/"
        response = api_key_client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.django_db
    def test_modules_v1_returns_404(self, api_key_client, workspace):
        url = f"/api/v1/workspaces/{workspace.slug}/projects/{self.FAKE_PROJECT_ID}/modules/"
        response = api_key_client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.django_db
    def test_stickies_v1_returns_404(self, api_key_client, workspace):
        url = f"/api/v1/workspaces/{workspace.slug}/stickies/"
        response = api_key_client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.contract
class TestKeptFeaturesStillWork:
    """Verify that core ticketing features are still accessible."""

    @pytest.mark.django_db
    def test_issues_endpoint_accessible(self, session_client, workspace):
        """Issues (work items) should still be accessible."""
        url = f"/api/workspaces/{workspace.slug}/projects/"
        response = session_client.get(url)
        # 200 OK means the endpoint is routed and working
        assert response.status_code == status.HTTP_200_OK

    @pytest.mark.django_db
    def test_workspace_endpoint_accessible(self, session_client, workspace):
        """Workspace endpoints should still be accessible."""
        url = f"/api/workspaces/{workspace.slug}/"
        response = session_client.get(url)
        assert response.status_code == status.HTTP_200_OK

    @pytest.mark.django_db
    def test_states_endpoint_accessible(self, session_client, workspace):
        """States endpoint should still be accessible."""
        url = f"/api/workspaces/{workspace.slug}/states/"
        response = session_client.get(url)
        assert response.status_code == status.HTTP_200_OK
