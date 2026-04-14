/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// Chat support disabled in this fork.

export interface IUseChatSupport {
  openChatSupport: () => void;
  isEnabled: boolean;
}

export const useChatSupport = (): IUseChatSupport => ({
  openChatSupport: () => {},
  isEnabled: false,
});
