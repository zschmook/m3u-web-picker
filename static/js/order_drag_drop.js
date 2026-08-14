(() => {
  "use strict";

  const modal = document.getElementById("orderModal");
  const tbody = document.getElementById("orderTable");
  if (!modal || !tbody || typeof renderOrderTable !== "function") return;

  const listWrap = modal.querySelector(".order-list-wrap");
  const headerRow = tbody.closest("table")?.querySelector("thead tr");
  const moveUpButton = document.getElementById("moveOrderUpBtn");
  const moveControls = moveUpButton?.parentElement;
  const helper = modal.querySelector(".modal-body > .small-muted.mt-2");

  if (moveControls) moveControls.remove();
  if (headerRow) {
    headerRow.innerHTML = `
      <th class="order-drag-column"><span class="visually-hidden">Reorder</span></th>
      <th style="width:60px;">#</th>
      <th>Channel</th>
      <th>Provider Group</th>`;
  }
  if (helper) helper.textContent = "Drag channels by the handle to reorder them, then save the manual channel order.";

  const style = document.createElement("style");
  style.id = "orderDragDropStyles";
  style.textContent = `
    #orderModal .order-drag-column,
    #orderModal .order-drag-cell {
      width: 46px;
      min-width: 46px;
      text-align: center;
    }

    #orderModal .order-drag-handle {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 32px;
      height: 32px;
      padding: 0;
      border: 0;
      border-radius: 7px;
      background: transparent;
      color: var(--ui-muted, #9ca3af);
      cursor: grab;
      font-size: 20px;
      line-height: 1;
      touch-action: none;
      user-select: none;
    }

    #orderModal .order-drag-handle:hover,
    #orderModal .order-drag-handle:focus-visible {
      color: var(--ui-heading, #f8fafc);
      background: color-mix(in srgb, var(--ui-accent, #3b82f6) 14%, transparent);
      outline: none;
    }

    #orderModal .order-drag-handle:focus-visible {
      box-shadow: 0 0 0 2px var(--ui-accent, #3b82f6);
    }

    #orderModal tr.order-dragging > td {
      background: color-mix(in srgb, var(--ui-accent, #3b82f6) 18%, var(--ui-panel, #1f2937)) !important;
      box-shadow: inset 0 2px 0 var(--ui-accent, #3b82f6), inset 0 -2px 0 var(--ui-accent, #3b82f6);
    }

    #orderModal tr.order-dragging .order-drag-handle,
    body.order-drag-active {
      cursor: grabbing !important;
    }

    #orderModal .order-list-wrap {
      scroll-behavior: auto;
    }
  `;
  document.head.appendChild(style);

  function handleMarkup(channel) {
    const name = escapeHtml(channel.name || channel.url || "channel");
    return `<button type="button" class="order-drag-handle" aria-label="Reorder ${name}" title="Drag to reorder; arrow keys also work"><span aria-hidden="true">⠿</span></button>`;
  }

  renderOrderTable = function renderOrderTableWithDragHandles() {
    tbody.innerHTML = orderChannels.map((channel, index) => `
      <tr data-key="${escapeHtml(channel.key)}" class="${channel.key === orderSelectedKey ? "order-selected" : ""}">
        <td class="order-drag-cell">${handleMarkup(channel)}</td>
        <td>${index + 1}</td>
        <td>${escapeHtml(channel.name || channel.url)}</td>
        <td>${escapeHtml(channel.group || "")}</td>
      </tr>`).join("");
  };

  function commitDomOrder(draggedKey) {
    const byKey = new Map(orderChannels.map(channel => [String(channel.key), channel]));
    const keys = [...tbody.querySelectorAll("tr[data-key]")].map(row => row.dataset.key);
    orderChannels = keys.map(key => byKey.get(String(key))).filter(Boolean);
    orderSelectedKey = draggedKey || "";
    renderOrderTable();
  }

  function focusHandleForKey(key) {
    const row = [...tbody.querySelectorAll("tr[data-key]")].find(candidate => candidate.dataset.key === key);
    row?.querySelector(".order-drag-handle")?.focus();
  }

  let dragState = null;
  let animationFrame = 0;

  function positionDraggedRow(clientY) {
    if (!dragState) return;
    const {row} = dragState;
    const otherRows = [...tbody.querySelectorAll("tr[data-key]")].filter(candidate => candidate !== row);
    const before = otherRows.find(candidate => {
      const rect = candidate.getBoundingClientRect();
      return clientY < rect.top + rect.height / 2;
    });
    if (before) tbody.insertBefore(row, before);
    else tbody.appendChild(row);
  }

  function dragFrame() {
    animationFrame = 0;
    if (!dragState || !listWrap) return;

    const rect = listWrap.getBoundingClientRect();
    const edge = Math.min(72, Math.max(44, rect.height * 0.12));
    const y = dragState.clientY;
    let delta = 0;

    if (y < rect.top + edge) {
      delta = -Math.ceil((rect.top + edge - y) / 5);
    } else if (y > rect.bottom - edge) {
      delta = Math.ceil((y - (rect.bottom - edge)) / 5);
    }

    if (delta) {
      listWrap.scrollTop += Math.max(-22, Math.min(22, delta));
      positionDraggedRow(y);
      animationFrame = requestAnimationFrame(dragFrame);
    }
  }

  function scheduleDragFrame() {
    if (!animationFrame) animationFrame = requestAnimationFrame(dragFrame);
  }

  function startDrag(event) {
    const handle = event.target.closest(".order-drag-handle");
    if (!handle) return;
    if (event.pointerType === "mouse" && event.button !== 0) return;

    const row = handle.closest("tr[data-key]");
    if (!row) return;

    event.preventDefault();
    dragState = {
      pointerId: event.pointerId,
      row,
      key: row.dataset.key,
      clientY: event.clientY,
    };
    orderSelectedKey = row.dataset.key;
    row.classList.add("order-dragging");
    document.body.classList.add("order-drag-active");
    handle.setPointerCapture?.(event.pointerId);
  }

  function moveDrag(event) {
    if (!dragState || event.pointerId !== dragState.pointerId) return;
    event.preventDefault();
    dragState.clientY = event.clientY;
    positionDraggedRow(event.clientY);
    scheduleDragFrame();
  }

  function finishDrag(event) {
    if (!dragState || (event.pointerId != null && event.pointerId !== dragState.pointerId)) return;
    const {row, key} = dragState;
    dragState = null;
    if (animationFrame) cancelAnimationFrame(animationFrame);
    animationFrame = 0;
    row.classList.remove("order-dragging");
    document.body.classList.remove("order-drag-active");
    commitDomOrder(key);
    requestAnimationFrame(() => focusHandleForKey(key));
  }

  tbody.addEventListener("pointerdown", startDrag);
  document.addEventListener("pointermove", moveDrag, {passive: false});
  document.addEventListener("pointerup", finishDrag);
  document.addEventListener("pointercancel", finishDrag);

  tbody.addEventListener("keydown", event => {
    const handle = event.target.closest(".order-drag-handle");
    if (!handle || !["ArrowUp", "ArrowDown"].includes(event.key)) return;

    const row = handle.closest("tr[data-key]");
    const key = row?.dataset.key;
    const index = orderChannels.findIndex(channel => String(channel.key) === String(key));
    const next = index + (event.key === "ArrowUp" ? -1 : 1);
    if (index < 0 || next < 0 || next >= orderChannels.length) return;

    event.preventDefault();
    const [item] = orderChannels.splice(index, 1);
    orderChannels.splice(next, 0, item);
    orderSelectedKey = key;
    renderOrderTable();
    requestAnimationFrame(() => focusHandleForKey(key));
  });
})();
