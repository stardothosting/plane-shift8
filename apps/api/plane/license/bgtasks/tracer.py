# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Telemetry removed from this fork. This task is kept as a no-op
# so that the celery beat schedule and register_instance don't break.

from celery import shared_task


@shared_task
def instance_traces():
    return
