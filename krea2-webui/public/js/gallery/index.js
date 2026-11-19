// ══════════════════════════════════════════════════════════════
// GALERÍA / FACADE — une store + toolbar + grid + viewer.
// tabs.js solo le pasa items y callbacks (redo, borrar, descargar).
// ══════════════════════════════════════════════════════════════
import { el } from "../ui.js";
import { createGalleryStore } from "./store.js";
import { renderToolbar, renderSelBar } from "./toolbar.js";
import { renderGrid, renderPager } from "./grid.js";
import { openViewer } from "./viewer.js";

export function createGallery({
  toolbarEl,
  selbarEl,
  gridEl,
  emptyEl,
  countEl,
  pagerEl,
  onRedo,
  onSendTo,
  onSendToUpscale,
  onDeleteOne,
  onDeleteMany,
  onDownloadMany,
} = {}) {
  const store = createGalleryStore({});
  let selectMode = false;
  let viewerList = []; // lista sobre la que navega el visor (la filtrada completa)

  function openAt(item) {
    viewerList = store.getFiltered();
    const idx = Math.max(0, viewerList.findIndex(g => g.id === item.id));
    openViewer({
      items: viewerList,
      index: idx,
      onRedo,
      onSendTo,
      onSendToUpscale,
      onDelete: onDeleteOne,
      onDownload: (it) => onDownloadMany && onDownloadMany([it]),
    });
  }

  function render() {
    const groups = store.getGroups();
    const c = store.counts();

    if (toolbarEl && !toolbarEl.dataset.wired) {
      toolbarEl.dataset.wired = "1";
      renderToolbar(toolbarEl, store, { onChange: render });
    }
    if (selbarEl) {
      renderSelBar(selbarEl, {
        active: selectMode,
        count: c.selected,
        onToggleMode: () => { selectMode = !selectMode; if (!selectMode) store.clearSelection(); render(); },
        onSelectAll: () => { store.selectVisible(); render(); },
        onClear: () => { store.clearSelection(); render(); },
        onDelete: () => onDeleteMany && onDeleteMany(store.selectedItems()),
        onDownload: () => onDownloadMany && onDownloadMany(store.selectedItems()),
      });
    }

    if (!c.filtered) {
      gridEl.innerHTML = "";
      if (pagerEl) pagerEl.innerHTML = "";
      emptyEl.classList.remove("hidden");
    } else {
      emptyEl.classList.add("hidden");
      renderGrid(gridEl, groups, {
        selectMode,
        isSelected: (id) => store.isSelected(id),
        onOpen: openAt,
        onToggleSelect: (it) => { store.toggleSelect(it.id); render(); },
        onRedo,
        onDelete: onDeleteOne,
      });
      if (pagerEl) {
        renderPager(pagerEl, {
          visible: c.visible,
          filtered: c.filtered,
          onMore: () => { store.more(); render(); },
        });
      }
    }
    if (countEl) countEl.textContent = `${c.filtered} imágenes${c.selected ? ` · ${c.selected} sel.` : ""}`;
  }

  return {
    store,
    render,
    setItems(items) { store.setItems(items); render(); },
    clearSelection() { store.clearSelection(); selectMode = false; render(); },
  };
}
