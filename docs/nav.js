/**
 * ECHO Documentation - nav.js v2.1
 * Génération sidebar + TOC dynamique imbriqué.
 * Aucun style inline - pilotage exclusif par classes CSS (style.css).
 */
document.addEventListener('DOMContentLoaded', () => {

  /* ============================================================
     0. MOBILE HEADER & OVERLAY
     ============================================================ */
  const mobileHeader = document.createElement('div');
  mobileHeader.className = 'mobile-header';
  mobileHeader.innerHTML = '<button class="menu-toggle">☰</button><span>ECHO v5 Docs</span>';
  document.body.prepend(mobileHeader);

  const sidebarOverlay = document.createElement('div');
  sidebarOverlay.className = 'sidebar-overlay';
  document.body.appendChild(sidebarOverlay);

  const menuBtn = mobileHeader.querySelector('.menu-toggle');
  menuBtn.addEventListener('click', () => {
    document.querySelector('.sidebar')?.classList.add('open');
    sidebarOverlay.classList.add('active');
  });

  sidebarOverlay.addEventListener('click', () => {
    document.querySelector('.sidebar')?.classList.remove('open');
    sidebarOverlay.classList.remove('active');
  });

  /* ============================================================
     1. GÉNÉRATION DE LA SIDEBAR
     ============================================================ */
  const sidebar = document.querySelector('.sidebar');
  if (sidebar) {
    const currentPage = window.location.pathname.split('/').pop() || 'index.html';
    const navItems = [
  {
    title: "Pôle I - Cortex Cognitif",
    items: [
      { text: "Accueil", href: "index.html" },
      { text: "1. Fondations & Philosophie", href: "01_fondations.html" },
      { text: "2. Manuel Utilisateur", href: "02_manuel_utilisateur.html" },
      { text: "3. Exemples de Prompts", href: "03_exemples_prompts.html" },
      { text: "4. Tutoriel Pratique", href: "04_tutoriel_pratique.html" },
      { text: "5. High-Level Design (HLD)", href: "05_hld_architecture.html" },
      { text: "6. Communication Gemini", href: "06_communication_gemini.html" },
      { text: "7. Écosystème HUD & UI", href: "07_hud_ui.html" },
      { text: "8. La Cognition", href: "08_cerveau_cognitif.html" },
      { text: "9. L'Arsenal des Outils", href: "09_arsenal_outils.html" },
      { text: "10. Planification & Exécution", href: "10_planification_execution.html" },
      { text: "11. Recherche & Navigation", href: "11_recherche_navigation.html" },
      { text: "12. Édition & Visuel", href: "12_edition_visuelle.html" },
      { text: "13. Mémoire & Actions", href: "13_memoire_actions.html" },
      { text: "14. Agents & Automatisation", href: "14_agents_automatisation.html" }
    ]
  },
  {
    title: "Pôle II - Topologie d'Infrastructure",
    items: [
      { text: "15. Déploiement & Infra", href: "15_deploiement.html" },
      { text: "16. Périphériques & Infra", href: "16_infrastructure_globale.html" },
      { text: "17. Inférence Distante", href: "17_edge_inference.html" },
      { text: "18. Annexes Techniques", href: "18_annexes.html" },
      { text: "19. Crédits", href: "19_credits.html" }
    ]
  }
];

    let html = `
      <div class="logo-container">
        <img src="logo-echo-medium.png" alt="ECHO Logo">
      </div>
      <h2>ECHO v5</h2>
      <div class="valve-container notranslate">
        <div class="valve-toggle" id="lang-toggle">
          <div class="valve-slider" id="valve-slider"></div>
          <div class="valve-option active" id="opt-fr">FR</div>
          <div class="valve-option" id="opt-en">EN</div>
        </div>
      </div>
      <div id="google_translate_element" style="display:none;"></div>
      <nav>`;

    navItems.forEach(pole => {
      html += `<div class="nav-pole-title">${pole.title}</div><ul>`;
      pole.items.forEach(item => {
        const isActive    = currentPage === item.href;
        const isSubActive = item.sub && item.sub.some(s => s.href === currentPage);
        const showSub     = isActive || isSubActive;

        html += `<li><a href="${item.href}" class="${isActive ? 'active' : ''}">${item.text}</a>`;
        if (item.sub) {
          html += `<ul class="sub-nav${showSub ? '' : ' hidden'}">`;
          item.sub.forEach(subItem => {
            const isSubItemActive = currentPage === subItem.href;
            html += `<li><a href="${subItem.href}" class="${isSubItemActive ? 'active' : ''}">${subItem.text}</a></li>`;
          });
          html += `</ul>`;
        }
        html += `</li>`;
      });
      html += `</ul>`;
    });

    sidebar.innerHTML = html + `</nav>`;
  }

  /* ============================================================
     2. GÉNÉRATION DU SOMMAIRE DYNAMIQUE IMBRIQUÉ (TOC)
     ============================================================ */
  const main = document.querySelector('main');
  if (!main) return;

  // Collecte tous les h2 et h3 du contenu principal
  const headings = Array.from(main.querySelectorAll('h2, h3'));
  if (headings.length >= 2) { // Bloc TOC si au moins 2 titres

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
  } // Fin du bloc TOC

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

  /* ============================================================
     4. MOTEUR DE TRADUCTION (Google Translate)
     ============================================================ */
  const gtScript = document.createElement('script');
  gtScript.type = 'text/javascript';
  // URL absolue obligatoire (https:) pour éviter les échecs sous file:///
  gtScript.src = 'https://translate.google.com/translate_a/element.js?cb=googleTranslateElementInit';
  document.head.appendChild(gtScript);

  window.googleTranslateElementInit = function() {
    new google.translate.TranslateElement({
      pageLanguage: 'fr', 
      includedLanguages: 'en,fr', 
      autoDisplay: false
    }, 'google_translate_element');
  };

  const langToggle = document.getElementById('lang-toggle');
  const optFr = document.getElementById('opt-fr');
  const optEn = document.getElementById('opt-en');

  if (langToggle) {
    const isEn = document.cookie.includes('googtrans=/fr/en') || window.location.hash.includes('googtrans');
    if (isEn) {
      langToggle.classList.add('is-en');
      optFr.classList.remove('active');
      optEn.classList.add('active');
    }

    langToggle.addEventListener('click', () => {
      const currentlyEn = langToggle.classList.contains('is-en');
      if (!currentlyEn) {
        // Switch to EN
        document.cookie = "googtrans=/fr/en; path=/";
        if (location.hostname) {
          document.cookie = "googtrans=/fr/en; domain=" + location.hostname + "; path=/";
        }
        window.location.hash = "#googtrans(fr|en)";
        window.location.reload();
      } else {
        // Switch to FR
        document.cookie = "googtrans=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;";
        document.cookie = "googtrans=; expires=Thu, 01 Jan 1970 00:00:00 UTC;";
        if (location.hostname) {
          const hostParts = location.hostname.split('.');
          for (let i = 0; i < hostParts.length; i++) {
            const domain = hostParts.slice(i).join('.');
            document.cookie = "googtrans=; expires=Thu, 01 Jan 1970 00:00:00 UTC; domain=" + domain + "; path=/;";
            document.cookie = "googtrans=; expires=Thu, 01 Jan 1970 00:00:00 UTC; domain=." + domain + "; path=/;";
          }
        }
        // Force URL without hash to clear Google Translate state completely
        window.history.replaceState({}, document.title, window.location.pathname + window.location.search);
        window.location.reload();
      }
    });
  }
});
