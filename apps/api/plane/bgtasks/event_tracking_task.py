# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Telemetry removed from this fork. This module is kept as a no-op
# so that existing callers (workspace views, auth utils) don't break.

import uuid
from typing import Dict, Any

from celery import shared_task


@shared_task
def track_event(user_id: uuid.UUID, event_name: str, slug: str, event_properties: Dict[str, Any]):
    return
