import { action, computed, makeObservable, observable, runInAction } from "mobx";
import set from "lodash/set";
// store
import { RootStore } from "store/root.store";
// types
import { TViewService } from "services/view/types";
import {
  TView,
  TViewFilters,
  TViewDisplayFilters,
  TViewDisplayProperties,
  TViewFilterProps,
  TViewFilterPartialProps,
  TViewAccess,
} from "@plane/types";
// helpers
import { FiltersHelper } from "./helpers/filters_helpers";

type TLoader = "submitting" | "submit" | undefined;

export type TViewStore = TView & {
  // observables
  loader: TLoader;
  filtersToUpdate: TViewFilterPartialProps;
  // computed
  appliedFilters: TViewFilterProps | undefined;
  appliedFiltersQueryParams: string | undefined;
  // helper actions
  updateFilters: (filters: Partial<TViewFilters>) => void;
  updateDisplayFilters: (display_filters: Partial<TViewDisplayFilters>) => void;
  updateDisplayProperties: (display_properties: Partial<TViewDisplayProperties>) => void;
  resetFilterChanges: () => void;
  saveFilterChanges: () => void;
  // actions
  lockView: () => Promise<void>;
  unlockView: () => Promise<void>;
  makeFavorite: () => Promise<void>;
  removeFavorite: () => Promise<void>;
  update: (viewData: Partial<TView>) => Promise<void>;
};

export class ViewStore extends FiltersHelper implements TViewStore {
  id: string | undefined;
  workspace: string | undefined;
  project: string | undefined;
  name: string | undefined;
  description: string | undefined;
  query: string | undefined;
  filters: TViewFilters | undefined;
  display_filters: TViewDisplayFilters | undefined;
  display_properties: TViewDisplayProperties | undefined;
  access: TViewAccess | undefined;
  owned_by: string | undefined;
  sort_order: number | undefined;
  is_locked: boolean | undefined;
  is_pinned: boolean | undefined;
  is_favorite: boolean | undefined;
  created_by: string | undefined;
  updated_by: string | undefined;
  created_at: Date | undefined;
  updated_at: Date | undefined;

  loader: TLoader = undefined;
  filtersToUpdate: TViewFilterPartialProps = {
    filters: {},
    display_filters: {},
    display_properties: {},
  };

  constructor(private store: RootStore, _view: TView, private service: TViewService) {
    super();
    this.id = _view.id;
    this.workspace = _view.workspace;
    this.project = _view.project;
    this.name = _view.name;
    this.description = _view.description;
    this.query = _view.query;
    this.filters = _view.filters ? this.computedFilters(_view.filters) : undefined;
    this.display_filters = _view.display_filters ? this.computedDisplayFilters(_view.display_filters) : undefined;
    this.display_properties = _view.display_properties
      ? this.computedDisplayProperties(_view.display_properties)
      : undefined;
    this.access = _view.access;
    this.owned_by = _view.owned_by;
    this.sort_order = _view.sort_order;
    this.is_locked = _view.is_locked;
    this.is_pinned = _view.is_pinned;
    this.is_favorite = _view.is_favorite;
    this.created_by = _view.created_by;
    this.updated_by = _view.updated_by;
    this.created_at = _view.created_at;
    this.updated_at = _view.updated_at;

    makeObservable(this, {
      // observables
      loader: observable,
      filtersToUpdate: observable.ref,
      // computed
      appliedFilters: computed,
      appliedFiltersQueryParams: computed,
      // helper actions
      updateFilters: action,
      updateDisplayFilters: action,
      updateDisplayProperties: action,
      resetFilterChanges: action,
      saveFilterChanges: action,
      // actions
      update: action,
      lockView: action,
      unlockView: action,
    });
  }

  // computed
  get appliedFilters() {
    return {
      filters: this.filters ? this.computedFilters(this.filters, this.filtersToUpdate.filters) : undefined,
      display_filters: this.display_filters
        ? this.computedDisplayFilters(this.display_filters, this.filtersToUpdate.display_filters)
        : undefined,
      display_properties: this.display_properties
        ? this.computedDisplayProperties(this.display_properties, this.filtersToUpdate.display_properties)
        : undefined,
    };
  }

  get appliedFiltersQueryParams() {
    const filters = this.appliedFilters;
    if (!filters) return undefined;
    return this.computeAppliedFiltersQueryParameters(filters, [])?.query || undefined;
  }

  // helper actions
  updateFilters = (filters: Partial<TViewFilters>) => {
    runInAction(() => {
      this.loader = "submit";
      this.filtersToUpdate.filters = filters;
    });
  };

  updateDisplayFilters = async (display_filters: Partial<TViewDisplayFilters>) => {
    const appliedFilters = this.appliedFilters;

    const layout = appliedFilters?.display_filters?.layout;
    const sub_group_by = appliedFilters?.display_filters?.sub_group_by;
    const group_by = appliedFilters?.display_filters?.group_by;
    const sub_issue = appliedFilters?.display_filters?.sub_issue;

    if (group_by === undefined) display_filters.sub_group_by = undefined;
    if (layout === "kanban") {
      if (sub_group_by === group_by) display_filters.group_by = undefined;
      if (group_by === null) display_filters.group_by = "state";
    }
    if (layout === "spreadsheet" && sub_issue === true) display_filters.sub_issue = false;

    runInAction(() => {
      this.loader = "submit";
      this.filtersToUpdate.display_filters = display_filters;
    });
  };

  updateDisplayProperties = async (display_properties: Partial<TViewDisplayProperties>) => {
    runInAction(() => {
      this.loader = "submit";
      this.filtersToUpdate.display_properties = display_properties;
    });
  };

  resetFilterChanges = () => {
    runInAction(() => {
      this.loader = undefined;
      this.filtersToUpdate = {
        filters: {},
        display_filters: {},
        display_properties: {},
      };
    });
  };

  saveFilterChanges = async () => {
    this.loader = "submitting";
    if (this.appliedFilters) await this.update(this.appliedFilters);
    this.loader = undefined;
  };

  // actions
  lockView = async () => {
    const { workspaceSlug, projectId } = this.store.app.router;
    if (!workspaceSlug || !this.id || !this.service.lock) return;

    const view = await this.service.lock(workspaceSlug, this.id, projectId);
    if (!view) return;

    runInAction(() => {
      this.is_locked = view.is_locked;
    });
  };

  unlockView = async () => {
    const { workspaceSlug, projectId } = this.store.app.router;
    if (!workspaceSlug || !this.id || !this.service.unlock) return;

    const view = await this.service.unlock(workspaceSlug, this.id, projectId);
    if (!view) return;

    runInAction(() => {
      this.is_locked = view.is_locked;
    });
  };

  makeFavorite = async () => {
    const { workspaceSlug, projectId } = this.store.app.router;
    if (!workspaceSlug || !this.id || !this.service.makeFavorite) return;

    const view = await this.service.makeFavorite(workspaceSlug, this.id, projectId);
    if (!view) return;

    runInAction(() => {
      this.is_favorite = view.is_locked;
    });
  };

  removeFavorite = async () => {
    const { workspaceSlug, projectId } = this.store.app.router;
    if (!workspaceSlug || !this.id || !this.service.removeFavorite) return;

    const view = await this.service.removeFavorite(workspaceSlug, this.id, projectId);
    if (!view) return;

    runInAction(() => {
      this.is_favorite = view.is_locked;
    });
  };

  update = async (viewData: Partial<TView>) => {
    const { workspaceSlug, projectId } = this.store.app.router;
    if (!workspaceSlug || !this.id) return;

    const view = await this.service.update(workspaceSlug, this.id, viewData, projectId);
    if (!view) return;

    runInAction(() => {
      Object.keys(viewData).forEach((key) => {
        const _key = key as keyof TView;
        set(this, _key, viewData[_key]);
      });
    });
  };
}
