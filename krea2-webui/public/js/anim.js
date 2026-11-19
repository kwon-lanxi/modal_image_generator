// ─────────────────────────────────────────────────────────────
// ANIMACIONES — anime.js (CDN, window.anime) con fallback a la
// Web Animations API nativa (funciona offline / sin CDN).
// Todas las funciones son seguras: si el elemento no existe, no pasa nada.
// ─────────────────────────────────────────────────────────────
function A() {
  return (typeof window !== "undefined" && typeof window.anime === "function") ? window.anime : null;
}

function wap(el, keyframes, { duration = 400, easing = "ease-out", delay = 0 } = {}) {
  if (!el || typeof el.animate !== "function") return null;
  try {
    return el.animate(keyframes, { duration, easing, delay, fill: "both" });
  } catch { return null; }
}

// Aparecer con pop suave (resultado de generación, modal)
export function animReveal(el, { delay = 0 } = {}) {
  if (!el) return;
  const a = A();
  if (a) {
    a({ targets: el, scale: [0.92, 1], opacity: [0, 1], translateY: [12, 0], duration: 480, delay, easing: "easeOutCubic" });
  } else {
    wap(el, [
      { opacity: 0, transform: "scale(.94) translateY(10px)" },
      { opacity: 1, transform: "scale(1) translateY(0)" },
    ], { duration: 450, delay });
  }
}

// Entrada escalonada (galería)
export function animStagger(list, { from = 24 } = {}) {
  const els = Array.from(list || []).filter(Boolean);
  if (!els.length) return;
  const a = A();
  if (a) {
    a({ targets: els, opacity: [0, 1], translateY: [from, 0], delay: a.stagger(45), duration: 380, easing: "easeOutQuad" });
  } else {
    els.forEach((e, i) => wap(e, [
      { opacity: 0, transform: `translateY(${from}px)` },
      { opacity: 1, transform: "translateY(0)" },
    ], { duration: 320, delay: i * 45 }));
  }
}

// Pulso corto (feedback de click / éxito)
export function animBurst(el) {
  if (!el) return;
  const a = A();
  if (a) {
    a({ targets: el, scale: [1, 1.06, 1], duration: 380, easing: "easeOutQuad" });
  } else {
    wap(el, [
      { transform: "scale(1)" },
      { transform: "scale(1.05)" },
      { transform: "scale(1)" },
    ], { duration: 380 });
  }
}

// Entrada de pestaña (slide + fade)
export function animTabIn(el) {
  if (!el) return;
  const a = A();
  if (a) {
    a({ targets: el, opacity: [0, 1], translateX: [14, 0], duration: 260, easing: "easeOutQuad" });
  } else {
    wap(el, [
      { opacity: 0, transform: "translateX(12px)" },
      { opacity: 1, transform: "translateX(0)" },
    ], { duration: 240 });
  }
}

// Toast: deslizar desde la derecha
export function animToast(el) {
  if (!el) return;
  const a = A();
  if (a) {
    a({ targets: el, translateX: [40, 0], opacity: [0, 1], duration: 260, easing: "easeOutCubic" });
  } else {
    wap(el, [
      { opacity: 0, transform: "translateX(32px)" },
      { opacity: 1, transform: "translateX(0)" },
    ], { duration: 250 });
  }
}

// Micro-oscileo para errores (no se usa todavía, listo para más adelante)
export function animShake(el) {
  if (!el) return;
  const a = A();
  if (a) {
    a({ targets: el, translateX: [0, -6, 6, -3, 3, 0], duration: 360, easing: "easeInOutQuad" });
  } else {
    wap(el, [
      { transform: "translateX(0)" }, { transform: "translateX(-6px)" },
      { transform: "translateX(6px)" }, { transform: "translateX(0)" },
    ], { duration: 360 });
  }
}
