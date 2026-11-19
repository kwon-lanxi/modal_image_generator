// ══════════════════════════════════════════════════════════════
// GALERÍA / TOOLBAR — búsqueda + filtros + orden + modo selección.
// Todo delega en el store; el re-render lo hace el facade (index.js).
// ══════════════════════════════════════════════════════════════
import { el } from "../ui.js";

function debounce(fn, ms) {
  let t = 0;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

export function renderToolbar(box, store, { onChange } = {}) {
  box.innerHTML = "";
  const fire = () => onChange && onChange();

  const search = el("input", {
    type: "search", class: "input max-w-xs", placeholder: "Buscar prompt, seed, archivo…",
    "aria-label": "Buscar en la galería por prompt, seed o archivo", value: store.state.q,
  });
  search.addEventListener("input", debounce((e) => { store.setQuery(e.target.value); fire(); }, 220));

  const source = el("select", { class: "input !w-auto", title: "Fuente", "aria-label": "Filtrar por fuente" }, [
    el("option", { value: "all", selected: store.state.source === "all" }, ["Todas"]),
    el("option", { value: "gen", selected: store.state.source === "gen" }, ["Generadas"]),
    el("option", { value: "beam", selected: store.state.source === "beam" }, ["Volume"]),
  ]);
  source.addEventListener("change", (e) => { store.setSource(e.target.value); fire(); });

  const sort = el("select", { class: "input !w-auto", title: "Orden", "aria-label": "Ordenar imágenes" }, [
    el("option", { value: "new", selected: store.state.sort === "new" }, ["Recientes"]),
    el("option", { value: "old", selected: store.state.sort === "old" }, ["Antiguas"]),
  ]);
  sort.addEventListener("change", (e) => { store.setSort(e.target.value); fire(); });

  box.append(el("div", { class: "gal-toolbar" }, [search, source, sort]));
}

// Barra de selección múltiple (visible solo en modo selección o con selección).
export function renderSelBar(box, {
  active = false,
  count = 0,
  onToggleMode,
  onSelectAll,
  onClear,
  onDelete,
  onDownload,
} = {}) {
  box.innerHTML = "";
  const toggle = el("button", {
    class: active ? "btn-primary !py-1.5 text-sm" : "btn-ghost !py-1.5 text-sm",
    html: active ? "✓ Selección" : "☐ Selección",
    title: "Modo selección múltiple", "aria-pressed": String(active),
    onclick: onToggleMode,
  });
  const row = el("div", { class: "gal-selbar" }, [toggle]);
  if (active || count > 0) {
    row.append(
      el("span", { class: "text-xs text-dim", role: "status", "aria-live": "polite", html: `${count} seleccionadas` }),
      el("button", { class: "btn-ghost !py-1 text-xs", html: "Todas", title: "Seleccionar visibles", onclick: onSelectAll }),
      el("button", { class: "btn-ghost !py-1 text-xs", html: "Ninguna", onclick: onClear }),
      count > 0 && el("button", { class: "btn-ghost !py-1 text-xs", html: "⬇ Descargar", title: "Descargar seleccionadas", onclick: onDownload }),
      count > 0 && el("button", { class: "btn-ghost btn-danger !py-1 text-xs", html: "🗑 Borrar", title: "Borrar seleccionadas", onclick: onDelete }),
    );
  }
  box.append(row);
}
