from __future__ import annotations

import pytest

from plane.db.models import (
    Estimate,
    EstimatePoint,
    Issue,
    IssueAssignee,
    IssueComment,
    IssueLabel,
    Label,
    Project,
    State,
    WorkspaceMember,
)
from plane.tests.factories import ProjectFactory, UserFactory, WorkspaceFactory

from plane_linear_import import entity_mapper as mapper
from plane_linear_import.importer import LinearImporter


class DummyClient:
    def __init__(self, comments=None):
        self._comments = comments or []

    def fetch_comments_for_issue(self, linear_issue_id):
        return self._comments


def _make_importer(workspace, *, comments=None, authoritative_sync=True):
    return LinearImporter(
        client=DummyClient(comments=comments),
        workspace_id=workspace.id,
        owner_id=workspace.owner_id,
        authoritative_sync=authoritative_sync,
    )


@pytest.mark.django_db
def test_authoritative_sync_reconciles_assignees_and_labels():
    workspace = WorkspaceFactory()
    WorkspaceMember.objects.get_or_create(workspace=workspace, member=workspace.owner, role=20)
    project = ProjectFactory(workspace=workspace, created_by=workspace.owner, updated_by=workspace.owner)
    state = State.objects.create(
        workspace=workspace,
        project=project,
        name="Todo",
        color="#000000",
        group="unstarted",
        created_by=workspace.owner,
        updated_by=workspace.owner,
    )
    issue = Issue.objects.create(
        workspace=workspace,
        project=project,
        state=state,
        name="Imported Issue",
        external_source=mapper.EXTERNAL_SOURCE,
        external_id="issue-1",
        created_by=workspace.owner,
        updated_by=workspace.owner,
    )

    keep_user = UserFactory()
    stale_user = UserFactory()
    IssueAssignee.objects.create(
        workspace=workspace,
        project=project,
        issue=issue,
        assignee=keep_user,
        created_by=workspace.owner,
        updated_by=workspace.owner,
    )
    stale_assignment = IssueAssignee.objects.create(
        workspace=workspace,
        project=project,
        issue=issue,
        assignee=stale_user,
        created_by=workspace.owner,
        updated_by=workspace.owner,
    )

    keep_label = mapper.map_label({"id": "l-keep", "name": "Keep", "color": "#111"})
    stale_label = mapper.map_label({"id": "l-stale", "name": "Stale", "color": "#222"})
    keep_label_obj = Label.objects.create(
        workspace=workspace,
        project=None,
        **keep_label,
        created_by=workspace.owner,
        updated_by=workspace.owner,
    )
    stale_label_obj = Label.objects.create(
        workspace=workspace,
        project=None,
        **stale_label,
        created_by=workspace.owner,
        updated_by=workspace.owner,
    )
    IssueLabel.objects.create(
        workspace=workspace,
        project=project,
        issue=issue,
        label=keep_label_obj,
        created_by=workspace.owner,
        updated_by=workspace.owner,
    )
    stale_issue_label = IssueLabel.objects.create(
        workspace=workspace,
        project=project,
        issue=issue,
        label=stale_label_obj,
        created_by=workspace.owner,
        updated_by=workspace.owner,
    )

    importer = _make_importer(workspace)
    importer._reconcile_issue_assignees(issue, project, keep_user.id)
    importer._reconcile_issue_labels(issue, project, [keep_label_obj.id])

    assert list(IssueAssignee.objects.filter(issue=issue).values_list("assignee_id", flat=True)) == [keep_user.id]
    assert IssueAssignee.all_objects.get(pk=stale_assignment.pk).deleted_at is not None
    assert list(IssueLabel.objects.filter(issue=issue).values_list("label_id", flat=True)) == [keep_label_obj.id]
    assert IssueLabel.all_objects.get(pk=stale_issue_label.pk).deleted_at is not None


@pytest.mark.django_db
def test_authoritative_sync_prunes_removed_comments():
    workspace = WorkspaceFactory()
    WorkspaceMember.objects.get_or_create(workspace=workspace, member=workspace.owner, role=20)
    project = ProjectFactory(workspace=workspace, created_by=workspace.owner, updated_by=workspace.owner)
    state = State.objects.create(
        workspace=workspace,
        project=project,
        name="Todo",
        color="#000000",
        group="unstarted",
        created_by=workspace.owner,
        updated_by=workspace.owner,
    )
    issue = Issue.objects.create(
        workspace=workspace,
        project=project,
        state=state,
        name="Imported Issue",
        external_source=mapper.EXTERNAL_SOURCE,
        external_id="issue-1",
        created_by=workspace.owner,
        updated_by=workspace.owner,
    )
    IssueComment.objects.create(
        workspace=workspace,
        project=project,
        issue=issue,
        comment_html="<p>keep</p>",
        comment_stripped="keep",
        external_source=mapper.EXTERNAL_SOURCE,
        external_id="c-keep",
        created_by=workspace.owner,
        updated_by=workspace.owner,
    )
    stale_comment = IssueComment.objects.create(
        workspace=workspace,
        project=project,
        issue=issue,
        comment_html="<p>stale</p>",
        comment_stripped="stale",
        external_source=mapper.EXTERNAL_SOURCE,
        external_id="c-stale",
        created_by=workspace.owner,
        updated_by=workspace.owner,
    )

    importer = _make_importer(
        workspace,
        comments=[
            {
                "id": "c-keep",
                "body": "keep updated",
                "createdAt": None,
                "updatedAt": None,
                "user": None,
            }
        ],
    )
    importer._import_comments("issue-1", issue.pk, project)

    active_comments = list(IssueComment.objects.filter(issue=issue).values_list("external_id", flat=True))
    assert active_comments == ["c-keep"]
    assert IssueComment.all_objects.get(pk=stale_comment.pk).deleted_at is not None


@pytest.mark.django_db
def test_authoritative_sync_clears_parent_and_estimate_when_removed():
    workspace = WorkspaceFactory()
    WorkspaceMember.objects.get_or_create(workspace=workspace, member=workspace.owner, role=20)
    project = ProjectFactory(workspace=workspace, created_by=workspace.owner, updated_by=workspace.owner)
    state = State.objects.create(
        workspace=workspace,
        project=project,
        name="Todo",
        color="#000000",
        group="unstarted",
        created_by=workspace.owner,
        updated_by=workspace.owner,
    )
    estimate = Estimate.objects.create(
        workspace=workspace,
        project=project,
        name="Linear Estimates",
        type="points",
        created_by=workspace.owner,
        updated_by=workspace.owner,
    )
    estimate_point = EstimatePoint.objects.create(
        workspace=workspace,
        project=project,
        estimate=estimate,
        key=0,
        value="1",
        created_by=workspace.owner,
        updated_by=workspace.owner,
    )
    parent_issue = Issue.objects.create(
        workspace=workspace,
        project=project,
        state=state,
        name="Parent",
        external_source=mapper.EXTERNAL_SOURCE,
        external_id="parent-1",
        created_by=workspace.owner,
        updated_by=workspace.owner,
    )
    issue = Issue.objects.create(
        workspace=workspace,
        project=project,
        state=state,
        parent=parent_issue,
        estimate_point=estimate_point,
        name="Child",
        external_source=mapper.EXTERNAL_SOURCE,
        external_id="issue-1",
        created_by=workspace.owner,
        updated_by=workspace.owner,
    )

    importer = _make_importer(workspace)
    importer._issue_map = {"parent-1": parent_issue.pk, "issue-1": issue.pk}
    importer._import_comments = lambda *args, **kwargs: None
    importer._import_attachments = lambda *args, **kwargs: None
    importer._import_relations = lambda *args, **kwargs: None

    importer._import_issues(
        [
            {
                "id": "issue-1",
                "identifier": "T1-1",
                "title": "Child updated",
                "description": "",
                "priority": 0,
                "state": {"id": state.external_id or str(state.id), "name": state.name},
                "assignee": None,
                "labels": {"nodes": []},
                "parent": None,
                "estimate": None,
                "createdAt": None,
                "updatedAt": None,
                "startedAt": None,
                "completedAt": None,
                "canceledAt": None,
                "dueDate": None,
                "url": "",
            }
        ],
        project,
        {state.external_id or str(state.id): state.pk},
        {},
    )

    issue.refresh_from_db()
    assert issue.parent_id is None
    assert issue.estimate_point_id is None