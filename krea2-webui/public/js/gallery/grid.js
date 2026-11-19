// ══════════════════════════════════════════════════════════════
// GALERÍA / GRID — grilla responsive de alturas variables con
// orden natural por filas (CSS grid, NO columns-*).
//
// Por qué no columns-*: ordena en vertical (columna 1 primero) y
// cada imagen que carga mueve el layout. Acá cada card reserva su
// espacio con aspect-ratio (ver card.js) y el orden es por filas.
// Columnas: auto-fill → 2 en celular, más en PC, sin breakpoints.
// ══════════════════════════════════════════════════════════════
import { el } from "../ui.js";
import { galCard } from "./card.js";

export function renderGrid(gridEl, groups, {
  selectMode = false,
  isSelected = () => false,
  onOpen,
  onToggleSelect,
  onRedo,
  onDelete,
} = {}) {
  gridEl.innerHTML = "";
  const frag = document.createDocumentFragment();
  for (const g of groups) {
    frag.append(el("div", { class: "gal-group" }, [
      el("div", { class: "gal-group-head" }, [
        el("span", { class: "gal-group-label", html: g.label }),
        el("span", { class: "gal-group-count", html: String(g.items.length) }),
      ]),
      (() => {
        const grid = el("div", { class: "gal-grid" });
        for (const it of g.items) {
          grid.append(galCard(it, {
            selected: isSelected(it.id),
            selectMode,
            onOpen,
            onToggleSelect,
            onRedo,
            onDelete,
          }));
        }
        return grid;
      })(),
    ]));
  }
  gridEl.append(frag);
}

// Pie: "mostrando X de Y" + botón cargar más.
export function renderPager(pagerEl, { visible, filtered, onMore } = {}) {
  pagerEl.innerHTML = "";
  if (!filtered) return;
  const row = el("div", { class: "gal-pager" }, [
    el("span", { class: "text-xs text-dim", html: `Mostrando ${visible} de ${filtered}` }),
  ]);
  if (visible < filtered) {
    row.append(el("button", {
      class: "btn-secondary !py-1.5 text-sm",
      html: `Cargar más (${filtered - visible} restantes)`,
      onclick: onMore,
    }));
  }
  pagerEl.append(row);
}
