/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
// plane imports
import { useTranslation } from "@plane/i18n";
// components
import { SettingsBoxedControlItem } from "@/components/settings/boxed-control-item";
import { SettingsHeading } from "@/components/settings/heading";
import { useInstance } from "@/hooks/store/use-instance";

export const BillingRoot = observer(function BillingRoot() {
  const { t } = useTranslation();
  const { config } = useInstance();
  const title = config?.is_self_managed ? "Instance" : t("workspace_settings.settings.billing_and_plans.heading");
  const description = config?.is_self_managed
    ? "This self-hosted instance is managed locally. No hosted upgrade flow is active on this fork."
    : t("workspace_settings.settings.billing_and_plans.description");

  return (
    <section className="relative scrollbar-hide size-full overflow-y-auto">
      <div>
        <SettingsHeading title={title} description={description} />
        <div className="mt-6">
          <SettingsBoxedControlItem
            title={config?.is_self_managed ? "Self Hosted Community" : "Community"}
            description={
              config?.is_self_managed
                ? "This instance is self-managed. Workspace access, authentication, and infrastructure are controlled by your deployment configuration."
                : "Unlimited projects, issues, cycles, modules, pages, and storage"
            }
          />
        </div>
      </div>
    </section>
  );
});
