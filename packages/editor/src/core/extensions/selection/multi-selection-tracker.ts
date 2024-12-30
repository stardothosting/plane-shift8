import { Extension } from "@tiptap/core";
import { Plugin, PluginKey } from "@tiptap/pm/state";
import { EditorView, Decoration, DecorationSet } from "@tiptap/pm/view";

const MultipleSelectionPluginKey = new PluginKey("multipleSelection");

interface BlockSelectionState {
  ranges: Array<[number, number]> | null;
  lastSelectedRange?: { from: number; to: number };
}

let activeCursor: HTMLElement | null = null;
const lastActiveSelection = {
  top: 0,
  left: 0,
};

let isDragging = false;
let selectNodesHandler: (event: MouseEvent) => void;
let scrollAnimationFrame: number | null = null;
let lastClientY = 0;
let currentScrollSpeed = 0;
const maxScrollSpeed = 20;
const acceleration = 0.5;
let cachedScrollParent = null;

function easeOutQuadAnimation(t: number) {
  return t * (2 - t);
}

const scrollParentCache = new WeakMap();

const getScrollParent = (element) => {
  if (cachedScrollParent) {
    return cachedScrollParent;
  }

  if (!element) return null;

  let currentParent = element.parentElement;

  while (currentParent) {
    if (isScrollable(currentParent)) {
      cachedScrollParent = currentParent;
      return cachedScrollParent;
    }
    currentParent = currentParent.parentElement;
  }

  cachedScrollParent = document.scrollingElement || document.documentElement;
  return cachedScrollParent;
};

const isScrollable = (node) => {
  if (!(node instanceof HTMLElement || node instanceof SVGElement)) {
    return false;
  }
  const style = getComputedStyle(node);
  return ["overflow", "overflow-y"].some((propertyName) => {
    const value = style.getPropertyValue(propertyName);
    return value === "auto" || value === "scroll";
  });
};

const updateCursorPosition = (event: MouseEvent, view: EditorView) => {
  if (!activeCursor) return;

  const x = event.pageX;
  const y = event.pageY;

  let newHeight = y - lastActiveSelection.top;
  let newWidth = x - lastActiveSelection.left;

  if (newHeight < 0) {
    activeCursor.style.marginTop = `${newHeight}px`;
    newHeight *= -1;
  } else {
    activeCursor.style.marginTop = "0px";
  }

  if (newWidth < 0) {
    activeCursor.style.marginLeft = `${newWidth}px`;
    newWidth *= -1;
  } else {
    activeCursor.style.marginLeft = "0px";
  }

  activeCursor.style.height = `${newHeight}px`;
  activeCursor.style.width = `${newWidth}px`;

  lastClientY = event.clientY;

  if (!scrollAnimationFrame) {
    scrollAnimationFrame = requestAnimationFrame(scroll);
  }
};

function scroll() {
  if (!isDragging) {
    currentScrollSpeed = 0;
    scrollAnimationFrame = null;
    return;
  }

  const editorContainer = document.querySelector(".editor-container"); // Assuming the editor container has a class 'ProseMirror'
  const scrollableParent = getScrollParent(editorContainer);

  if (!scrollableParent) {
    scrollAnimationFrame = requestAnimationFrame(scroll);
    return;
  }

  console.log("scrollableParent", scrollableParent);
  const scrollRegionUp = 150;
  const scrollRegionDown = scrollableParent.clientHeight - 100;

  let targetScrollAmount = 0;

  if (lastClientY < scrollRegionUp) {
    const ratio = easeOutQuadAnimation((scrollRegionUp - lastClientY) / scrollRegionUp);
    targetScrollAmount = -maxScrollSpeed * ratio;
  } else if (lastClientY > scrollRegionDown) {
    const ratio = easeOutQuadAnimation(
      (lastClientY - scrollRegionDown) / (scrollableParent.clientHeight - scrollRegionDown)
    );
    targetScrollAmount = maxScrollSpeed * ratio;
  }

  currentScrollSpeed += (targetScrollAmount - currentScrollSpeed) * acceleration;

  if (Math.abs(currentScrollSpeed) > 0.1) {
    scrollableParent.scrollBy(0, currentScrollSpeed);
  }

  scrollAnimationFrame = requestAnimationFrame(scroll);
}

const getNodesInRect = (view: EditorView, rect: DOMRect) => {
  const ranges: Array<[number, number]> = [];
  const { doc } = view.state;

  const isInRect = (pos: number) => {
    const coords = view.coordsAtPos(pos);
    return (
      coords &&
      coords.top < rect.bottom &&
      coords.bottom > rect.top &&
      coords.left < rect.right &&
      coords.right > rect.left
    );
  };

  doc.nodesBetween(0, doc.content.size, (node, pos) => {
    const resolvedPos = view.state.doc.resolve(pos);

    if (resolvedPos.depth === 1) {
      const start = resolvedPos.before(1);
      const end = resolvedPos.after(1);

      if (isInRect(start) || isInRect(end)) {
        ranges.push([start, end]);
      }

      return false;
    }
    return true;
  });

  return ranges;
};

const removeActiveUser = (view?: EditorView) => {
  if (!activeCursor) return;

  activeCursor.remove();
  activeCursor = null;
  document.removeEventListener("mousemove", selectNodesHandler);
  document.removeEventListener("mouseup", () => removeActiveUser(view));

  if (scrollAnimationFrame) {
    cancelAnimationFrame(scrollAnimationFrame);
    scrollAnimationFrame = null;
  }

  isDragging = false;
};

const createMultipleSelectionPlugin = () =>
  new Plugin<BlockSelectionState>({
    key: MultipleSelectionPluginKey,
    state: {
      init() {
        return { ranges: null };
      },
      apply(tr, state) {
        const action = tr.getMeta(MultipleSelectionPluginKey);
        if (action) {
          return action;
        }
        return state;
      },
    },
    props: {
      handleDOMEvents: {
        mousedown(view: EditorView, event: MouseEvent) {
          if (!isDragging) {
            const pluginState = MultipleSelectionPluginKey.getState(view.state);
            if (pluginState?.ranges?.length || pluginState?.lastSelectedRange) {
              view.dispatch(
                view.state.tr.setMeta(MultipleSelectionPluginKey, {
                  ranges: null,
                  lastSelectedRange: undefined,
                })
              );
              return false;
            }
          }

          if (event.target !== view.dom) {
            return false;
          }
          removeActiveUser(view);

          activeCursor = document.createElement("div");
          activeCursor.className = "multipleSelectionCursor";
          activeCursor.style.width = "0px";
          activeCursor.style.height = "0px";
          activeCursor.style.borderRadius = "2px";
          activeCursor.style.border = "1px solid rgba(var(--color-primary-100), 0.2)";
          activeCursor.style.background = "rgba(var(--color-primary-100), 0.2)";
          activeCursor.style.opacity = "0.5";
          activeCursor.style.position = "absolute";
          activeCursor.style.top = `${event.pageY}px`;
          activeCursor.style.left = `${event.pageX}px`;
          activeCursor.style.pointerEvents = "none";
          activeCursor.style.zIndex = "1000";

          lastActiveSelection.top = event.pageY;
          lastActiveSelection.left = event.pageX;

          document.body.appendChild(activeCursor);

          isDragging = true;
          lastClientY = event.clientY;

          selectNodesHandler = (e: MouseEvent) => {
            updateCursorPosition(e, view);
            if (activeCursor) {
              const rect = activeCursor.getBoundingClientRect();
              const ranges = getNodesInRect(view, rect);

              view.dispatch(
                view.state.tr.setMeta(MultipleSelectionPluginKey, {
                  ranges,
                  lastSelectedRange: undefined,
                })
              );
            }
          };

          document.addEventListener("mousemove", selectNodesHandler);
          document.addEventListener("mouseup", () => removeActiveUser(view));

          event.preventDefault();
          return true;
        },
      },
      decorations(state) {
        const pluginState = this.getState(state);

        if (pluginState?.ranges?.length) {
          return DecorationSet.create(
            state.doc,
            pluginState.ranges.map(([from, to]) =>
              Decoration.node(from, to, {
                class: "ProseMirror-selectednode",
              })
            )
          );
        }

        if (pluginState?.lastSelectedRange) {
          const { from, to } = pluginState.lastSelectedRange;
          return DecorationSet.create(state.doc, [
            Decoration.node(from, to, {
              class: "ProseMirror-selectednode",
            }),
          ]);
        }

        return null;
      },
    },
    view() {
      return {
        destroy() {
          removeActiveUser();
        },
      };
    },
  });

export const multipleSelectionExtension = Extension.create({
  name: "multipleSelection",

  addProseMirrorPlugins() {
    return [createMultipleSelectionPlugin()];
  },
});
