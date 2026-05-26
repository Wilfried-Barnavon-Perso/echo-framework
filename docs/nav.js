/**
 * ECHO Documentation — nav.js v2.0
 * Génération sidebar + TOC dynamique imbriqué.
 * Aucun style inline — pilotage exclusif par classes CSS (style.css).
 */
document.addEventListener('DOMContentLoaded', () => {

  /* ============================================================
     1. GÉNÉRATION DE LA SIDEBAR
     ============================================================ */
  const sidebar = document.querySelector('.sidebar');
  if (sidebar) {
    const currentPage = window.location.pathname.split('/').pop() || 'index.html';
    const navItems = [
      { href: 'index.html',              text: 'Introduction' },
      { href: '00_fondations.html',      text: '0. Fondations & Philosophie' },
      { href: '01_hld_architecture.html',text: '1. High-Level Design (HLD)' },
      { href: '02_deploiement.html',     text: '2. Déploiement & Infra' },
      { href: '03_echo_libs.html',       text: '3. Librairies Partagées' },
      { href: '04_hud_ui.html',          text: '4. Écosystème HUD & UI' },
      { href: '05_filtre.html',          text: '5. Les Filtres (Conscience)' },
      { href: '06_pipe.html',            text: '6. Le Pipe (Cortex)' },
      {
        href: '07_arsenal_outils.html',
        text: "7. L'Arsenal des Outils",
        sub: [
          { href: '07a_web_intelligence.html',  text: '7a. Web Intelligence' },
          { href: '07b_vault_explorer.html',    text: "7b. Explorateur de l'Espace Personnel" },
          { href: '07c_memory_cognition.html',  text: '7c. Mémoire & Cognition' },
          { href: '07d_execution_monitoring.html', text: '7d. Exécution & Pilotage' },
          { href: '07e_actions_ui.html',        text: '7e. Actions UI (HUD)' },
          { href: '07f_visual_intelligence.html', text: '7f. Visual Intelligence' }
        ]
      },
      { href: '08_system_prompt.html',   text: '8. Le Kernel (System Prompt)' },
      { href: '09_admin_manager.html',   text: '9. Admin Manager' },
      { href: '10_annexes.html',         text: '10. Annexes Techniques' }
    ];

    let html = `
      <div class="logo-container">
        <img src="logo-echo.png" alt="ECHO Logo">
      </div>
      <h2>ECHO v5</h2>
      <nav><ul>`;

    navItems.forEach(item => {
      const isActive    = currentPage === item.href;
      const isSubActive = item.sub && item.sub.some(s => s.href === currentPage);
      const showSub     = isActive || isSubActive;

      html += `<li><a href="${item.href}" class="${isActive ? 'active' : ''}">${item.text}</a>`;
      if (item.sub) {
        // Classe CSS 'hidden' au lieu de style="display:none"
        html += `<ul class="sub-nav${showSub ? '' : ' hidden'}">`;
        item.sub.forEach(subItem => {
          const isSubItemActive = currentPage === subItem.href;
          html += `<li><a href="${subItem.href}" class="${isSubItemActive ? 'active' : ''}">${subItem.text}</a></li>`;
        });
        html += `</ul>`;
      }
      html += `</li>`;
    });

    sidebar.innerHTML = html + `</ul></nav>`;
  }

  /* ============================================================
     2. GÉNÉRATION DU SOMMAIRE DYNAMIQUE IMBRIQUÉ (TOC)
     ============================================================ */
  const main = document.querySelector('main');
  if (!main) return;

  // Collecte tous les h2 et h3 du contenu principal
  const headings = Array.from(main.querySelectorAll('h2, h3'));
  if (headings.length < 2) return; // Pas de TOC si moins de 2 titres

  // Injection de la div TOC dans le DOM
  const tocEl = document.createElement('nav');
  tocEl.id = 'page-toc';
  tocEl.setAttribute('aria-label', 'Sommaire');

  // Slug URL-safe depuis le texte
  const toSlug = (text) =>
    text.toLowerCase()
        .normalize('NFD').replace(/[\u0300-\u036f]/g, '') // Suppression accents
        .replace(/[^a-z0-9\s-]/g, '')
        .trim().replace(/\s+/g, '-');

  let tocHTML = '<h3>Sommaire</h3><ol>';
  let currentH2Li = null;
  let currentSubList = null;
  let h2Count = 0;

  headings.forEach(heading => {
    // Ajout d'un id si absent
    if (!heading.id) {
      heading.id = toSlug(heading.textContent);
    }
    const text = heading.textContent.trim();
    const id   = heading.id;

    if (heading.tagName === 'H2') {
      h2Count++;
      if (currentSubList) tocHTML += '</ol></li>';
      tocHTML += `<li><a href="#${id}">${text}</a>`;
      currentSubList = null;
    } else if (heading.tagName === 'H3') {
      if (!currentSubList) {
        tocHTML += '<ol class="toc-sub">';
        currentSubList = true;
      }
      tocHTML += `<li><a href="#${id}">${text}</a></li>`;
    }
  });
  if (currentSubList) tocHTML += '</ol>';
  tocHTML += '</li></ol>';

  tocEl.innerHTML = tocHTML;
  document.body.appendChild(tocEl);

  /* ---- Surlignage de la section active au scroll ---- */
  const tocLinks = tocEl.querySelectorAll('a');
  const headingEls = headings;

  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          tocLinks.forEach(l => l.classList.remove('toc-active'));
          const active = tocEl.querySelector(`a[href="#${entry.target.id}"]`);
          if (active) active.classList.add('toc-active');
        }
      });
    },
    { rootMargin: '-10% 0px -80% 0px', threshold: 0 }
  );
  headingEls.forEach(h => observer.observe(h));

  /* ============================================================
     3. MOTEUR ZOOM UNIVERSEL (Mermaid + SVG + Images)
     ============================================================ */
  const modal = document.createElement('div');
  modal.className = 'modal-overlay';
  modal.innerHTML = `
    <div class="zoom-control">
      <label id="zoom-label">140%</label>
      <input type="range" id="zoom-slider" min="50" max="300" value="140">
    </div>
    <span class="modal-close">&times;</span>
    <div class="modal-content"><div id="modal-svg-container"></div></div>`;
  document.body.appendChild(modal);

  const modalContent     = modal.querySelector('.modal-content');
  const modalSvgContainer = modal.querySelector('#modal-svg-container');
  const closeBtn         = modal.querySelector('.modal-close');
  const zoomSlider       = modal.querySelector('#zoom-slider');
  const zoomLabel        = modal.querySelector('#zoom-label');

  let isDragging = false;
  let startX, startY, scrollLeft, scrollTop;

  const updateZoom = (val) => {
    const factor = val / 100;
    zoomLabel.innerText = val + '%';
    const target = modalSvgContainer.querySelector('svg, img');
    if (!target) return;
    const cx = (modal.scrollLeft + window.innerWidth  / 2) / modalContent.offsetWidth;
    const cy = (modal.scrollTop  + window.innerHeight / 2) / modalContent.offsetHeight;
    target.style.transform = `scale(${factor})`;
    setTimeout(() => {
      modal.scrollLeft = cx * modalContent.offsetWidth  - window.innerWidth  / 2;
      modal.scrollTop  = cy * modalContent.offsetHeight - window.innerHeight / 2;
    }, 0);
  };

  const openModal = (element) => {
    let content = '';
    if (element.tagName === 'svg' || element.querySelector('svg')) {
      const svg = (element.tagName === 'svg' ? element : element.querySelector('svg')).cloneNode(true);
      svg.removeAttribute('width');
      svg.removeAttribute('height');
      svg.style.width  = '80vw';
      svg.style.height = 'auto';
      content = svg.outerHTML;
    } else if (element.tagName === 'IMG') {
      content = `<img src="${element.src}" style="width:80vw;height:auto;" alt="">`;
    }
    if (!content) return;
    modalSvgContainer.innerHTML = content;
    modal.style.display = 'block';
    document.body.style.overflow = 'hidden';
    zoomSlider.value = 140;
    updateZoom(140);
    setTimeout(() => {
      modal.scrollLeft = (modalContent.offsetWidth  - window.innerWidth)  / 2;
      modal.scrollTop  = (modalContent.offsetHeight - window.innerHeight) / 2;
    }, 10);
  };

  const closeModal = () => {
    modal.style.display = 'none';
    document.body.style.overflow = '';
    modalSvgContainer.innerHTML = '';
  };

  zoomSlider.addEventListener('input', (e) => updateZoom(e.target.value));

  // Drag-to-pan
  modal.addEventListener('mousedown', (e) => {
    if (e.target.closest('.modal-close') || e.target.closest('.zoom-control')) return;
    isDragging = true;
    startX = e.pageX - modal.offsetLeft;
    startY = e.pageY - modal.offsetTop;
    scrollLeft = modal.scrollLeft;
    scrollTop  = modal.scrollTop;
  });
  modal.addEventListener('mouseleave', () => isDragging = false);
  modal.addEventListener('mouseup',    () => isDragging = false);
  modal.addEventListener('mousemove',  (e) => {
    if (!isDragging) return;
    e.preventDefault();
    const x = e.pageX - modal.offsetLeft;
    const y = e.pageY - modal.offsetTop;
    modal.scrollLeft = scrollLeft - (x - startX);
    modal.scrollTop  = scrollTop  - (y - startY);
  });

  // Clic pour ouvrir (mermaid-wrapper, svg-diagram, images)
  document.addEventListener('click', (e) => {
    if (modal.style.display === 'block') return;
    const wrapper = e.target.closest('.mermaid-wrapper, .svg-diagram');
    if (wrapper) { openModal(wrapper); return; }
    if (e.target.tagName === 'IMG' && !e.target.closest('.sidebar')) openModal(e.target);
  });

  closeBtn.addEventListener('click', (e) => { e.stopPropagation(); closeModal(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModal(); });
});
