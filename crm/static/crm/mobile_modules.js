/* Category presentation only; module destinations and app routing stay native. */
(() => {
  'use strict';
  document.querySelectorAll('[data-modules-root]').forEach(root => {
    if (root.dataset.modulesEnhanced === '1') return;
    const overview = root.querySelector('[data-modules-overview]');
    const overviewTitle = overview?.querySelector('h3');
    const navigation = root.querySelector('[data-modules-group-navigation]');
    const back = navigation?.querySelector('[data-modules-back]');
    const groups = [...root.querySelectorAll('[data-modules-group]')];
    const buttons = [...root.querySelectorAll('[data-modules-category-target]')];
    const groupsById = new Map(groups.map(group => [group.id, group]));
    if (!overview || !overviewTitle || !navigation || !back || !groups.length || buttons.length !== groups.length || buttons.some(button => !groupsById.has(button.dataset.modulesCategoryTarget))) return;
    let activeGroup = null;

    function showCategories(focus = false) {
      groups.forEach(group => { group.hidden = true; });
      buttons.forEach(button => button.setAttribute('aria-expanded', 'false'));
      overview.hidden = false;
      navigation.hidden = true;
      activeGroup = null;
      root.dataset.modulesView = 'categories';
      if (focus) overviewTitle.focus();
    }

    function showGroup(button) {
      const group = groupsById.get(button.dataset.modulesCategoryTarget);
      if (!group) return;
      overview.hidden = true;
      navigation.hidden = false;
      groups.forEach(item => { item.hidden = item !== group; });
      buttons.forEach(item => item.setAttribute('aria-expanded', item === button ? 'true' : 'false'));
      activeGroup = group;
      root.dataset.modulesView = 'group';
      group.querySelector('[data-modules-group-title]')?.focus();
    }

    buttons.forEach(button => button.addEventListener('click', () => showGroup(button)));
    back.addEventListener('click', () => showCategories(true));
    root.addEventListener('keydown', event => {
      if (event.key !== 'Escape' || !activeGroup || event.defaultPrevented) return;
      event.preventDefault();
      event.stopPropagation();
      showCategories(true);
    });
    root.dataset.modulesEnhanced = '1';
    showCategories();

    // Watch this panel only. Closing the embedded screen resets its categories
    // without listening to, changing, or replacing the main app navigation.
    if (root.dataset.modulesEmbedded === '1') {
      new MutationObserver(() => {
        if (!root.classList.contains('active')) showCategories();
      }).observe(root, { attributes: true, attributeFilter: ['class'] });
    }
  });
})();
