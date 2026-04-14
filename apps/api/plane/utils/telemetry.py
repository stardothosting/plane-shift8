# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Telemetry removed from this fork. This module is kept as a no-op
# so that existing callers (tracer bgtask, register_instance) don't break.


def init_tracer():
    return None


def shutdown_tracer():
    return
