// ══════════════════════════════════════════════════════════════
// GALERÍA / STORE — lógica pura, sin DOM.
// Filtra, busca, ordena, pagina y selecciona sobre la lista de items
// que arma tabs.js (local + D1 + volume). Testeable en node.
// ══════════════════════════════════════════════════════════════

export const PAGE_SIZE_DEFAULT = 60;

// ts normalizado (ms epoch). Acepta número, string ISO o null.
export function itemTs(it) {
  const v = it?.created_at;
  if (typeof v === "number" && isFinite(v)) return v;
  if (v) {
    const t = new Date(v).getTime();
    if (isFinite(t)) return t;
  }
  return 0;
}

// Texto buscable: prompts + seed + filename + tab.
function searchableText(it) {
  const p = it?.payload || {};
  return [
    p.positive_prompt || "",
    p.edit_prompt || "",
    p.negative_prompt || "",
    p.seed ?? "",
    it?.beam_filename || "",
    it?.tab || "",
  ].join(" ").toLowerCase();
}

function startOfDay(ts) {
  const d = new Date(ts);
  d.setHours(0, 0, 0, 0);
  return d.getTime();
}

// Etiqueta de grupo por fecha: Hoy / Ayer / "12 ago" / "12 ago 2025".
export function dateGroupLabel(ts, now = Date.now()) {
  if (!ts) return "Sin fecha";
  const day = startOfDay(ts);
  const today = startOfDay(now);
  const diffDays = Math.round((today - day) / 86400000);
  if (diffDays <= 0) return "Hoy";
  if (diffDays === 1) return "Ayer";
  const d = new Date(ts);
  const s = d.toLocaleDateString("es-AR", { day: "numeric", month: "short" });
  return d.getFullYear() === new Date(now).getFullYear() ? s : `${s} ${d.getFullYear()}`;
}

export function createGalleryStore({ pageSize = PAGE_SIZE_DEFAULT } = {}) {
  const s = {
    items: [],
    q: "",
    tab: "",          // "" = todas (respeta el filtro legacy de tabs.js)
    source: "all",    // all | gen | beam
    sort: "new",      // new | old
    visibleCount: pageSize,
    pageSize,
    selected: new Set(), // ids
  };

  function filtered() {
    const q = s.q.trim().toLowerCase();
    let out = s.items;
    if (s.tab) out = out.filter(g => g.tab === s.tab && g.source !== "beam");
    if (s.source === "gen") out = out.filter(g => g.source !== "beam");
    else if (s.source === "beam") out = out.filter(g => g.source === "beam");
    if (q) out = out.filter(g => searchableText(g).includes(q));
    out = out.slice().sort((a, b) =>
      s.sort === "old" ? itemTs(a) - itemTs(b) : itemTs(b) - itemTs(a));
    return out;
  }

  return {
    // ── escritura ──
    setItems(items) {
      s.items = Array.isArray(items) ? items : [];
      // Podar selección de ids que ya no existen
      if (s.selected.size) {
        const ids = new Set(s.items.map(g => g.id));
        for (const id of [...s.selected]) if (!ids.has(id)) s.selected.delete(id);
      }
    },
    setQuery(q) { s.q = String(q || ""); s.visibleCount = s.pageSize; },
    setTab(t) { s.tab = String(t || ""); s.visibleCount = s.pageSize; },
    setSource(v) { s.source = v || "all"; s.visibleCount = s.pageSize; },
    setSort(v) { s.sort = v === "old" ? "old" : "new"; s.visibleCount = s.pageSize; },
    resetPaging() { s.visibleCount = s.pageSize; },
    more() { s.visibleCount += s.pageSize; },

    // ── selección ──
    toggleSelect(id) {
      if (s.selected.has(id)) s.selected.delete(id);
      else s.selected.add(id);
    },
    selectVisible() { for (const g of filtered().slice(0, s.visibleCount)) s.selected.add(g.id); },
    clearSelection() { s.selected.clear(); },
    get selectedCount() { return s.selected.size; },
    isSelected(id) { return s.selected.has(id); },
    selectedItems() {
      const byId = new Map(s.items.map(g => [g.id, g]));
      return [...s.selected].map(id => byId.get(id)).filter(Boolean);
    },

    // ── lectura ──
    getFiltered() { return filtered(); },
    getVisible() { return filtered().slice(0, s.visibleCount); },
    // Grupos por fecha sobre lo visible: [{ key, label, items }]
    getGroups(now) {
      const groups = [];
      const byKey = new Map();
      for (const it of filtered().slice(0, s.visibleCount)) {
        const ts = itemTs(it);
        const key = ts ? String(startOfDay(ts)) : "nodate";
        if (!byKey.has(key)) {
          const g = { key, label: dateGroupLabel(ts, now), items: [] };
          byKey.set(key, g);
          groups.push(g);
        }
        byKey.get(key).items.push(it);
      }
      return groups;
    },
    counts() {
      const f = filtered().length;
      return {
        total: s.items.length,
        filtered: f,
        visible: Math.min(f, s.visibleCount),
        selected: s.selected.size,
      };
    },
    get state() { return s; },
  };
}
