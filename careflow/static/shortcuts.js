/**
 * CareFlow Vim/Gmail-Style Keyboard Shortcuts for Rapid Navigator Case Triage
 *
 * Supported Keys:
 *   j / ArrowDown : Move selection down to next case in worklist
 *   k / ArrowUp   : Move selection up to previous case in worklist
 *   Enter / o     : Open selected case detail page
 *   e             : Trigger escalate action on active case
 *   a             : Trigger draft approve action
 *   ?             : Show/hide keyboard shortcuts cheat sheet
 *   Escape        : Dismiss modal dialog
 */

(function () {
  'use strict';

  let selectedIndex = -1;

  function getCaseRows() {
    return Array.from(document.querySelectorAll('tbody tr.case-row, table.worklist-table tbody tr'));
  }

  function highlightRow(rows, index) {
    rows.forEach((r, idx) => {
      if (idx === index) {
        r.classList.add('selected-case-row');
        r.style.outline = '2px solid #2563eb';
        r.style.backgroundColor = '#eff6ff';
        r.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
      } else {
        r.classList.remove('selected-case-row');
        r.style.outline = '';
        r.style.backgroundColor = '';
      }
    });
  }

  function toggleShortcutsModal() {
    const modal = document.getElementById('shortcuts-modal');
    if (!modal) return;
    if (modal.style.display === 'flex' || modal.style.display === 'block') {
      modal.style.display = 'none';
    } else {
      modal.style.display = 'flex';
    }
  }

  document.addEventListener('keydown', function (e) {
    // Ignore keystrokes when typing in inputs, textareas, or selects
    const tag = (e.target.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea' || tag === 'select' || e.target.isContentEditable) {
      if (e.key === 'Escape') {
        e.target.blur();
      }
      return;
    }

    const rows = getCaseRows();

    switch (e.key) {
      case 'j':
      case 'ArrowDown':
        if (rows.length > 0) {
          e.preventDefault();
          selectedIndex = Math.min(selectedIndex + 1, rows.length - 1);
          highlightRow(rows, selectedIndex);
        }
        break;

      case 'k':
      case 'ArrowUp':
        if (rows.length > 0) {
          e.preventDefault();
          selectedIndex = Math.max(selectedIndex - 1, 0);
          highlightRow(rows, selectedIndex);
        }
        break;

      case 'Enter':
      case 'o':
        if (selectedIndex >= 0 && selectedIndex < rows.length) {
          const link = rows[selectedIndex].querySelector('a');
          if (link && link.href) {
            e.preventDefault();
            window.location.href = link.href;
          }
        }
        break;

      case 'e':
        // Escalate trigger
        const escalateBtn = document.querySelector("button[name='action'][value='unreachable'], button.action-escalate, [data-action='escalate']");
        if (escalateBtn) {
          e.preventDefault();
          escalateBtn.click();
        }
        break;

      case 'a':
        // Approve draft trigger
        const approveBtn = document.querySelector("form.approve-form button[type='submit'], button.action-approve");
        if (approveBtn) {
          e.preventDefault();
          approveBtn.click();
        }
        break;

      case '?':
        e.preventDefault();
        toggleShortcutsModal();
        break;

      case 'Escape':
        const modal = document.getElementById('shortcuts-modal');
        if (modal) modal.style.display = 'none';
        const supervisorModal = document.getElementById('supervisor-modal');
        if (supervisorModal) supervisorModal.style.display = 'none';
        break;
    }
  });

  // Attach button click on footer keyboard shortcuts trigger
  window.addEventListener('DOMContentLoaded', () => {
    const trigger = document.getElementById('shortcuts-trigger');
    if (trigger) {
      trigger.addEventListener('click', toggleShortcutsModal);
    }
  });
})();
